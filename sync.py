import os, glob, re, subprocess

print("==================================================")
print("  APPLYING COMPACT PORTAL STANDARD BUTTON LAYOUT  ")
print("==================================================")

student_clean_buttons = '''
<div class="dashboard-welcome-hub" role="region" aria-label="Quick Actions" style="margin: 0 0 16px; padding: 0; background: transparent; border: none; box-shadow: none;">
  <div class="welcome-hub-actions" style="display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 0; padding: 0;">
    <a href="/online-compiler" class="hub-btn hub-btn-primary" style="display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px; margin: 0; border-radius: 8px; font-size: 13px; font-weight: 650; text-decoration: none; background: linear-gradient(135deg, var(--accent, #6366f1), var(--accent-strong, #4f46e5)); color: #ffffff; border: 1px solid #4f46e5; box-shadow: 0 2px 8px color-mix(in srgb, var(--accent-strong, #6366f1) 25%, transparent); white-space: nowrap; cursor: pointer;">
      <span>💻 Open SQL Compiler</span>
    </a>
    <a href="/portal/daily-tasks/" class="hub-btn hub-btn-secondary" style="display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px; margin: 0; border-radius: 8px; font-size: 13px; font-weight: 650; text-decoration: none; background: var(--surface, #0c1727); color: var(--text, #dce6f4); border: 1px solid var(--border, #263850); box-shadow: 0 2px 6px rgba(0,0,0,0.2); white-space: nowrap; cursor: pointer;">
      <span>📋 Daily Tasks</span>
    </a>
    <a href="/portal/tests/" class="hub-btn hub-btn-secondary" style="display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px; margin: 0; border-radius: 8px; font-size: 13px; font-weight: 650; text-decoration: none; background: var(--surface, #0c1727); color: var(--text, #dce6f4); border: 1px solid var(--border, #263850); box-shadow: 0 2px 6px rgba(0,0,0,0.2); white-space: nowrap; cursor: pointer;">
      <span>🎯 Tests</span>
    </a>
  </div>
</div>
'''

teacher_clean_buttons = '''
<div class="teacher-quick-actions" role="region" aria-label="Teacher Actions" style="display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 0 0 16px; padding: 0;">
  <a class="teacher-action-chip chip-primary" href="/portal/daily-tasks/?view=review#submission-review" style="display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px; margin: 0; border-radius: 8px; font-size: 13px; font-weight: 650; text-decoration: none; background: linear-gradient(135deg, var(--accent, #6366f1), var(--accent-strong, #4f46e5)); color: #ffffff; border: 1px solid #4f46e5; box-shadow: 0 2px 8px color-mix(in srgb, var(--accent-strong, #6366f1) 25%, transparent); white-space: nowrap; cursor: pointer;"><span class="chip-icon">📥</span> Review Submissions</a>
  <a class="teacher-action-chip" href="/portal/daily-tasks/?view=create" style="display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px; margin: 0; border-radius: 8px; font-size: 13px; font-weight: 650; text-decoration: none; background: var(--surface, #0c1727); color: var(--text, #dce6f4); border: 1px solid var(--border, #263850); box-shadow: 0 2px 6px rgba(0,0,0,0.2); white-space: nowrap; cursor: pointer;"><span class="chip-icon">＋</span> Create Daily Task</a>
  <a class="teacher-action-chip" href="/portal/tests/#create-test" style="display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px; margin: 0; border-radius: 8px; font-size: 13px; font-weight: 650; text-decoration: none; background: var(--surface, #0c1727); color: var(--text, #dce6f4); border: 1px solid var(--border, #263850); box-shadow: 0 2px 6px rgba(0,0,0,0.2); white-space: nowrap; cursor: pointer;"><span class="chip-icon">🎯</span> Create Test</a>
  <a class="teacher-action-chip" href="/portal/teacher/activity/" style="display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px; margin: 0; border-radius: 8px; font-size: 13px; font-weight: 650; text-decoration: none; background: var(--surface, #0c1727); color: var(--text, #dce6f4); border: 1px solid #263850; box-shadow: 0 2px 6px rgba(0,0,0,0.2); white-space: nowrap; cursor: pointer;"><span class="chip-icon">🟢</span> Live Activity</a>
  <a class="teacher-action-chip" href="/portal/teacher/attendance/" style="display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px; margin: 0; border-radius: 8px; font-size: 13px; font-weight: 650; text-decoration: none; background: var(--surface, #0c1727); color: var(--text, #dce6f4); border: 1px solid #263850; box-shadow: 0 2px 6px rgba(0,0,0,0.2); white-space: nowrap; cursor: pointer;"><span class="chip-icon">📊</span> Attendance</a>
  <a class="teacher-action-chip" href="/portal/teacher/history/" style="display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px; margin: 0; border-radius: 8px; font-size: 13px; font-weight: 650; text-decoration: none; background: var(--surface, #0c1727); color: var(--text, #dce6f4); border: 1px solid #283e58; box-shadow: 0 2px 6px rgba(0,0,0,0.2); white-space: nowrap; cursor: pointer;"><span class="chip-icon">📜</span> SQL History</a>
</div>
'''

portal_style = '''
<style id="portalActionChipsStyle">
  .welcome-hub-title, .welcome-hub-pill, .dashboard-welcome-hub h2, .dashboard-welcome-hub p { display: none !important; }
  .dashboard-welcome-hub { background: transparent !important; border: none !important; padding: 0 !important; margin: 0 0 16px !important; box-shadow: none !important; }

  .student-quick-actions, .teacher-quick-actions, .welcome-hub-actions {
    display: flex !important;
    flex-wrap: wrap !important;
    gap: 10px !important;
    align-items: center !important;
    margin: 0 0 16px !important;
    padding: 0 !important;
  }

  .student-action-chip, .teacher-action-chip, .hub-btn {
    display: inline-flex !important;
    align-items: center !important;
    gap: 8px !important;
    padding: 8px 16px !important;
    margin: 0 !important;
    border-radius: 8px !important;
    font-size: 13px !important;
    font-weight: 650 !important;
    text-decoration: none !important;
    background: var(--surface, #0c1727) !important;
    color: var(--text, #dce6f4) !important;
    border: 1px solid var(--border, #263850) !important;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.2) !important;
    transition: color 0.15s ease, background-color 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease !important;
    white-space: nowrap !important;
    cursor: pointer !important;
  }

  .student-action-chip:hover, .teacher-action-chip:hover, .hub-btn:hover {
    transform: translateY(-1px) !important;
    border-color: var(--accent-strong, #6366f1) !important;
    background-color: color-mix(in srgb, var(--accent-strong, #6366f1) 18%, var(--surface, #0c1727)) !important;
    color: #ffffff !important;
    box-shadow: 0 4px 14px color-mix(in srgb, var(--accent-strong, #6366f1) 30%, #000000) !important;
    text-decoration: none !important;
  }

  .student-action-chip:focus-visible, .teacher-action-chip:focus-visible, .hub-btn:focus-visible {
    outline: 2px solid var(--accent-strong, #6366f1) !important;
    outline-offset: 2px !important;
  }

  .student-action-chip.chip-primary, .teacher-action-chip.chip-primary, .hub-btn-primary {
    background: linear-gradient(135deg, var(--accent, #6366f1), var(--accent-strong, #4f46e5)) !important;
    color: #ffffff !important;
    border: 1px solid var(--accent-strong, #4f46e5) !important;
    box-shadow: 0 2px 8px color-mix(in srgb, var(--accent-strong, #6366f1) 25%, transparent) !important;
  }

  .student-action-chip.chip-primary:hover, .teacher-action-chip.chip-primary:hover, .hub-btn-primary:hover {
    filter: brightness(1.1) !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 6px 18px color-mix(in srgb, var(--accent-strong, #6366f1) 45%, transparent) !important;
    color: #ffffff !important;
  }

  .chip-icon, .hub-btn span {
    text-decoration: none !important;
  }
</style>
'''

files = glob.glob('**/*.html', recursive=True)
count = 0
for f in files:
    try:
        with open(f, 'r', encoding='utf-8') as fp:
            c = fp.read()
        orig = c
        if 'student-dashboard' in f:
            c = re.sub(r'<div class="dashboard-welcome-hub"[\s\S]*?</div>\s*</div>\s*</div>', student_clean_buttons, c)
            c = re.sub(r'<div class="welcome-hub-title">[\s\S]*?</div>\s*', '', c)
            c = re.sub(r'<style id="portalActionChipsStyle">[\s\S]*?</style>', '', c)
            if '{% endblock %}' in c:
                c = c.replace('{% endblock %}', portal_style + '\n{% endblock %}', 1)
            else:
                c += '\n' + portal_style
            if c != orig:
                with open(f, 'w', encoding='utf-8') as fp:
                    fp.write(c)
                print(f"  [✓] Updated Student Dashboard: {f}")
                count += 1
        elif 'teacher-dashboard' in f:
            c = re.sub(r'<div class="teacher-quick-actions"[\s\S]*?</div>', teacher_clean_buttons, c)
            c = re.sub(r'<style id="portalActionChipsStyle">[\s\S]*?</style>', '', c)
            if '{% endblock %}' in c:
                c = c.replace('{% endblock %}', portal_style + '\n{% endblock %}', 1)
            else:
                c += '\n' + portal_style
            if c != orig:
                with open(f, 'w', encoding='utf-8') as fp:
                    fp.write(c)
                print(f"  [✓] Updated Teacher Dashboard: {f}")
                count += 1
        elif 'base-dashboard' in f:
            c = re.sub(r'<style id="portalActionChipsStyle">[\s\S]*?</style>', '', c)
            if '</head>' in c:
                c = c.replace('</head>', portal_style + '\n</head>')
            else:
                c += '\n' + portal_style
            if c != orig:
                with open(f, 'w', encoding='utf-8') as fp:
                    fp.write(c)
                print(f"  [✓] Updated Base Dashboard: {f}")
                count += 1
    except Exception as e:
        pass

print(f"\n[+] Total Templates Updated: {count}")
print("  ✓ Spacing set to standard 10px gap (no stacked margins)")
print("  ✓ Bottom margin reduced to clean 16px")
print("  ✓ Portal standard 8px padding and border-radius applied")

if os.path.exists("service.ps1"):
    print("[*] Restarting local server service...")
    try:
        subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", "service.ps1", "-Action", "RestartOnly"], check=False)
        print("  [✓] Server restarted successfully!")
    except Exception as e:
        pass

print("==================================================")
print("  🎉 COMPLETE! Browser me jakar Ctrl + F5 dabayein!")
print("==================================================")
