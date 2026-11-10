"""Extract question/reference pairs without running imported SQL or instructions."""
import csv
import io
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree

MARKER = re.compile(r"^\s*(?:\#{1,6}\s*)?(?:\*\*)?(reference\s+sql\s+answer|reference\s+sql|sql\s+statement|sql\s+answer|answer\s+sql|answer|ans|solution|verification\s+sql|test\s+calls|starter\s+(?:sql|code))\s*(?:\*\*)?\s*(?:(?::\s*[-–—]?|[-–—=]+)\s*(?:\*\*)?\s*(.*)|(?:\*\*)?\s*$)", re.I)
QUESTION = re.compile(r"^\s*(?:\*\*)?(?:(?:question|q)\s*)?\d+\s*[.):\-](?:\s+|$)(.*)", re.I)


def _record(prompt, expected_sql="", starter_sql="", verification_sql=""):
    return dict(prompt=prompt.strip(), expected_sql=expected_sql.strip(), starter_sql=starter_sql.strip(), verification_sql=verification_sql.strip())


def _limit(records):
    if not records:
        raise ValueError("No questions were found in that file.")
    if len(records) > 200:
        raise ValueError("Import at most 200 questions at a time.")
    if any(not item["prompt"] for item in records):
        raise ValueError("Every imported answer must have a question before it.")
    return records


class _SQLState:
    def __init__(self):
        self.quote=None
        self.qclose=None
        self.block=False

    @property
    def open(self):
        return self.quote is not None or self.block

    def feed(self, text):
        i=0
        while i < len(text):
            if self.block:
                end=text.find("*/",i)
                if end < 0: return
                self.block=False;i=end+2
            elif self.qclose:
                end=text.find(self.qclose+"'",i)
                if end < 0: return
                self.quote=None;self.qclose=None;i=end+2
            elif self.quote:
                if text[i]==self.quote:
                    if i+1 < len(text) and text[i+1]==self.quote: i+=2;continue
                    self.quote=None
                i+=1
            elif text.startswith("--",i):
                end=text.find("\n",i)
                if end < 0: return
                i=end+1
            elif text.startswith("/*",i):
                self.block=True;i+=2
            elif text[i:i+2].lower()=="q'" and i+2 < len(text):
                opening=text[i+2];self.qclose={"[":"]","(":")","{":"}","<":">"}.get(opening,opening)
                self.quote="q";i+=3
            elif text[i] in ("'", '"'):
                self.quote=text[i];i+=1
            else: i+=1


def parse_text(text):
    text=text.lstrip("\ufeff")
    lines=text.splitlines()
    if not any(MARKER.match(line) for line in lines):
        from .portal_views import _questions_from_text
        return [_record(prompt) for prompt in _questions_from_text(text)]
    records=[]
    fields={key:[] for key in ("prompt","expected_sql","starter_sql","verification_sql")}
    section="prompt";fenced=False;seen_answer=False;sql_state=_SQLState()
    numbered=any(QUESTION.match(line) for line in lines)
    started=not numbered

    def flush():
        nonlocal fields,seen_answer
        if any(any(part.strip() for part in value) for value in fields.values()):
            record=_record(**{key:"\n".join(value) for key,value in fields.items()})
            if seen_answer and not record["expected_sql"]:
                raise ValueError("An answer heading has no SQL beneath it. Add the reference answer before importing.")
            records.append(record)
            if len(records) > 200: raise ValueError("Import at most 200 questions at a time.")
        fields={key:[] for key in fields};seen_answer=False

    for raw in lines:
        protected=section!="prompt" and sql_state.open
        question=QUESTION.match(raw) if not fenced and not protected else None
        if question:
            flush();section="prompt";started=True;sql_state=_SQLState()
            fields[section].append(question.group(1).removesuffix("**").strip())
            continue
        if not started: continue
        marker=MARKER.match(raw) if not fenced and not protected else None
        if marker:
            label=marker.group(1).lower()
            section="verification_sql" if label.startswith(("verification","test")) else "starter_sql" if label.startswith("starter") else "expected_sql"
            seen_answer=seen_answer or section=="expected_sql"
            sql_state=_SQLState()
            if marker.group(2):
                fields[section].append(marker.group(2))
                sql_state.feed(marker.group(2)+"\n")
        elif re.match(r"^\s*```(?:sql|plsql)?\s*$",raw,re.I) and section!="prompt" and not protected:
            fenced=not fenced
        else:
            fields[section].append(raw)
            if section!="prompt": sql_state.feed(raw+"\n")
    flush()
    return _limit(records)


def parse_upload(upload):
    if upload.size > 2*1024*1024:
        raise ValueError("Question files must be 2 MB or smaller.")
    suffix=Path(upload.name or "").suffix.lower()
    try:
        if suffix==".txt": return parse_text(upload.read().decode("utf-8-sig"))
        if suffix==".csv":
            rows=list(csv.reader(io.StringIO(upload.read().decode("utf-8-sig"))))
            rows=[row for row in rows if row and row[0].strip()]
            if not rows: return _limit([])
            headers=[re.sub(r"[_\s]+"," ",cell.strip().lower()) for cell in rows[0]]
            known={"question","questions","prompt","instructions"}
            has_header=headers[0] in known
            answer_names={"answer","ans","sql statement","expected sql","reference sql","reference sql answer"}
            def index(names): return next((i for i,value in enumerate(headers) if value in names),None)
            answer=index(answer_names) if has_header else 1
            starter=index({"starter sql","starter code"}) if has_header else None
            verification=index({"verification sql","test calls"}) if has_header else None
            result=[]
            for row in rows[1:] if has_header else rows:
                cell=lambda i:row[i].strip() if i is not None and i < len(row) else ""
                if answer is not None and cell(answer):
                    result.append(_record(row[0],cell(answer),cell(starter),cell(verification)))
                else: result.extend(parse_text(row[0]))
            return _limit(result)
        if suffix==".docx":
            with zipfile.ZipFile(upload) as docx:
                info=docx.getinfo("word/document.xml")
                if info.file_size > 4*1024*1024: raise ValueError("The document contains too much text to import.")
                root=ElementTree.fromstring(docx.read(info))
            ns="{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            paragraphs=[]
            for paragraph in root.iter(ns+"p"):
                paragraphs.append("".join(node.text or "" if node.tag==ns+"t" else "\n" if node.tag in {ns+"br",ns+"cr"} else "\t" if node.tag==ns+"tab" else "" for node in paragraph.iter()))
            return parse_text("\n".join(paragraphs))
        raise ValueError("Use a .txt, .csv, or .docx question file.")
    except (UnicodeDecodeError,csv.Error,zipfile.BadZipFile,KeyError,ElementTree.ParseError,RuntimeError,OSError):
        raise ValueError("The question file could not be read. Check its format and try again.") from None


from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST


@login_required(login_url="/portal/")
@require_POST
def preview_upload(request):
    from .portal_views import _active_portal_role
    from .views import same_origin
    if _active_portal_role(request)!="TEACHER" or not same_origin(request):
        return JsonResponse({"error":"Teacher access from this portal is required."},status=403)
    upload=request.FILES.get("question_file")
    if not upload: return JsonResponse({"error":"Choose a question file."},status=400)
    try: records=parse_upload(upload)
    except ValueError as exc: return JsonResponse({"error":str(exc)},status=400)
    response=JsonResponse({"questions":records})
    response["Cache-Control"]="no-store"
    return response
