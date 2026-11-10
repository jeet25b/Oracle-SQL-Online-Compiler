(() => {
  const form = document.querySelector("[data-enroll-form]");
  if (!form) return;

  const search = form.querySelector("[data-roster-search]");
  const list = form.querySelector("[data-roster-suggestions]");
  const selectedBox = form.querySelector("[data-roster-selected]");
  const select = form.querySelector("select[multiple]");
  if (!search || !list || !selectedBox || !select) return;

  const selected = new Map(
    [...select.options].filter((option) => option.selected)
      .map((option) => [option.value, { id: option.value, name: option.textContent, accountId: "" }]),
  );
  let requestController = null;
  let debounceTimer = null;
  let activeIndex = -1;

  const closeList = () => {
    list.hidden = true;
    list.replaceChildren();
    search.setAttribute("aria-expanded", "false");
    search.removeAttribute("aria-activedescendant");
    activeIndex = -1;
  };

  const syncSelect = () => {
    [...select.options].forEach((option) => { option.selected = selected.has(option.value); });
    selectedBox.replaceChildren();
    selected.forEach((student, id) => {
      const chip = document.createElement("span");
      chip.className = "roster-chip";
      const label = document.createElement("span");
      label.textContent = student.accountId ? `${student.name} · ${student.accountId}` : student.name;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "roster-chip-remove";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Remove ${student.name}`);
      remove.addEventListener("click", () => {
        selected.delete(id);
        syncSelect();
        search.focus();
      });
      chip.append(label, remove);
      selectedBox.append(chip);
    });
  };

  const choose = (student) => {
    selected.set(String(student.id), student);
    let option = [...select.options].find((item) => item.value === String(student.id));
    if (!option) {
      option = new Option(student.name, String(student.id));
      select.add(option);
    }
    syncSelect();
    search.value = "";
    closeList();
    search.focus();
  };

  const setActive = (index) => {
    const options = [...list.querySelectorAll("[role=option]")];
    if (!options.length) return;
    activeIndex = (index + options.length) % options.length;
    options.forEach((option, optionIndex) => {
      option.setAttribute("aria-selected", optionIndex === activeIndex ? "true" : "false");
    });
    const active = options[activeIndex];
    search.setAttribute("aria-activedescendant", active.id);
    active.scrollIntoView({ block: "nearest" });
  };

  const showStudents = (students) => {
    list.replaceChildren();
    students.filter((student) => !selected.has(String(student.id))).forEach((student) => {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "roster-suggestion";
      option.setAttribute("role", "option");
      option.id = `roster-option-${student.id}`;
      option.setAttribute("aria-selected", "false");
      const name = document.createElement("strong");
      name.textContent = student.name;
      const meta = document.createElement("span");
      meta.textContent = [student.accountId, student.email].filter(Boolean).join(" · ");
      option.append(name, meta);
      option.addEventListener("mousedown", (event) => event.preventDefault());
      option.addEventListener("click", () => choose(student));
      list.append(option);
    });
    if (!list.childElementCount) {
      const empty = document.createElement("p");
      empty.className = "roster-empty";
      empty.textContent = "No matching students found.";
      list.append(empty);
    }
    list.hidden = false;
    search.setAttribute("aria-expanded", "true");
    activeIndex = -1;
  };

  search.addEventListener("input", () => {
    const query = search.value.trim();
    if (requestController) requestController.abort();
    window.clearTimeout(debounceTimer);
    if (query.length < 2) {
      closeList();
      return;
    }
    debounceTimer = window.setTimeout(async () => {
      requestController = new AbortController();
      const params = new URLSearchParams({ class: form.dataset.classId || "", q: query });
      list.replaceChildren();
      const loading = document.createElement("p");
      loading.className = "roster-empty";
      loading.textContent = "Searching students…";
      list.replaceChildren(loading);
      list.hidden = false;
      search.setAttribute("aria-expanded", "true");
      try {
        const response = await fetch(`/portal/classes/students/search/?${params}`, {
          credentials: "same-origin",
          headers: { Accept: "application/json" },
          signal: requestController.signal,
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || "Student search failed.");
        if (search.value.trim() === query) showStudents(payload.students || []);
      } catch (error) {
        if (error.name === "AbortError") return;
        list.replaceChildren();
        const message = document.createElement("p");
        message.className = "roster-empty roster-error";
        message.textContent = error.message || "Could not search students.";
        list.append(message);
        list.hidden = false;
        search.setAttribute("aria-expanded", "true");
      }
    }, 180);
  });

  search.addEventListener("keydown", (event) => {
    const options = list.querySelectorAll("[role=option]");
    if (event.key === "ArrowDown" && options.length) {
      event.preventDefault();
      setActive(activeIndex + 1);
    } else if (event.key === "ArrowUp" && options.length) {
      event.preventDefault();
      setActive(activeIndex < 0 ? options.length - 1 : activeIndex - 1);
    } else if (event.key === "Enter" && !list.hidden && options.length) {
      event.preventDefault();
      const active = options[activeIndex < 0 ? 0 : activeIndex];
      active.click();
    } else if (event.key === "Escape") {
      closeList();
    }
  });

  search.addEventListener("focus", () => {
    if (search.value.trim().length >= 2) search.dispatchEvent(new Event("input"));
  });
  document.addEventListener("click", (event) => {
    if (!form.contains(event.target)) closeList();
  });
  form.querySelector('input[type="file"]')?.addEventListener("change", (event) => {
    const input = event.currentTarget;
    const small = input.closest(".academic-field")?.querySelector("small");
    if (small && input.files?.[0]) small.textContent = `${input.files[0].name} selected. Students will be added together with any selected above.`;
  });

  syncSelect();
})();
