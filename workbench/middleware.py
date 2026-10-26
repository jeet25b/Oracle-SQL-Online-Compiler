"""Apply the shared login UI policy to current and future HTML pages."""
from django.http import HttpResponse


class LoginAutofillPolicyMiddleware:
    script = b'<script src="/portal/assets/login-autofill.js?v=20261019-6" defer></script>'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (request.method == "HEAD" or response.status_code != 200
                or "text/html" not in response.get("Content-Type", "")
                or response.get("Content-Encoding")):
            return response
        # HTML FileResponses are small local templates; other streaming responses
        # (downloads, event streams) never enter this branch.
        if response.streaming:
            if not getattr(response, "file_to_stream", None):
                return response
            original = response
            content = b"".join(original.streaming_content)
            response = HttpResponse(content, status=original.status_code,
                                    headers=dict(original.headers))
            response.cookies = original.cookies
            original.file_to_stream.close()
        content = response.content
        if b"/portal/assets/login-autofill.js" not in content:
            position = content.lower().find(b"</head>")
            if position >= 0:
                response.content = content[:position] + self.script + content[position:]
                if response.has_header("Content-Length"):
                    response["Content-Length"] = str(len(response.content))
                for header in ("ETag", "Content-MD5"):
                    if response.has_header(header):
                        del response[header]
        return response
