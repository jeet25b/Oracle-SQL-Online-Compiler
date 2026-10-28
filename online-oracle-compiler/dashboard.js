(() => {
  const date = document.querySelector('#todayDate');
  const year = document.querySelector('#year');
  const today = new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric' }).format(new Date());
  if (date) date.textContent = today;
  if (year) year.textContent = new Date().getFullYear();

  const toast = document.querySelector('#toast');
  document.querySelectorAll('.compiler-selection-card').forEach((card) => {
    card.addEventListener('click', () => {
      if (card.dataset.ready === 'true' && card.dataset.href) {
        window.location.assign(card.dataset.href);
        return;
      }
      if (toast) {
        toast.textContent = `${card.dataset.compilerName || 'This compiler'} is listed, but its database driver and execution backend are not configured yet. Ask an administrator to configure the engine before using it.`;
        toast.hidden = false;
      }
    });
  });
  const dashboardLinks = [...document.querySelectorAll('[data-dashboard-link]')];
  const normalizePath = (path) => path.replace(/\/+$/, '') || '/';
  const currentPath = normalizePath(window.location.pathname);
  const setActiveDashboardLink = (activeLink) => {
    document.querySelectorAll('.side-nav .side-link').forEach((link) => {
      const active = link === activeLink;
      link.classList.toggle('active', active);
      if (active) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
  };
  dashboardLinks.forEach((link) => {
    link.addEventListener('click', (event) => {
      if (!link.hash) return;
      const linkPath = normalizePath(new URL(link.href, window.location.href).pathname);
      if (linkPath !== currentPath) return;
      const target = document.getElementById(decodeURIComponent(link.hash.slice(1)));
      if (!target) {
        event.preventDefault();
        toast.textContent = 'This dashboard section is not available.';
        toast.hidden = false;
        return;
      }
      setActiveDashboardLink(link);
    });
  });
  const initialDashboardLink = dashboardLinks.find((link) => {
    if (link.hash) return link.hash === window.location.hash;
    const targetPath = new URL(link.href, window.location.href).pathname.replace(/\/+$/, '') || '/';
    return targetPath === currentPath;
  });
  if (initialDashboardLink) setActiveDashboardLink(initialDashboardLink);
  if (document.body.dataset.userRole === 'STUDENT') {
    const requestKey = 'compiler-edit-permission-prompt-seen';
    const pollEditPermissionRequest = async () => {
      if (document.hidden) return;
      try {
        const response = await fetch('/api/compiler/editor-share', { credentials: 'same-origin', cache: 'no-store' });
        if (!response.ok) return;
        const data = await response.json();
        const request = data.editPermissionRequest;
        if (!request?.id || sessionStorage.getItem(requestKey) === request.id) return;
        sessionStorage.setItem(requestKey, request.id);
        const approved = window.confirm(`${request.teacherName} is requesting permission to edit your SQLCL and SQL WorkSheet editors for this session. Allow access?`);
        const token = sessionStorage.getItem('compiler-editor-share-session') || (crypto.randomUUID ? crypto.randomUUID().replaceAll('-', '') : `${Date.now()}${Math.random().toString(36).slice(2)}`);
        sessionStorage.setItem('compiler-editor-share-session', token);
        const result = await fetch('/api/compiler/editor-share', {
          method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action: 'respond_edit_permission', requestId: request.id, approved, sessionToken: token }),
        });
        if (!result.ok) sessionStorage.removeItem(requestKey);
        else if (approved && toast) {
          toast.textContent = 'Your teacher can edit your shared editors for this session.';
          toast.hidden = false;
        }
      } catch { sessionStorage.removeItem(requestKey); /* Retry the permission prompt check on the next interval. */ }
    };
    pollEditPermissionRequest();
    window.setInterval(pollEditPermissionRequest, 5000);
  }
  window.addEventListener('hashchange', () => {
    const link = dashboardLinks.find((item) => item.hash && item.hash === window.location.hash);
    if (link) setActiveDashboardLink(link);
  });
  const roleChoiceError = document.querySelector('[data-role-choice-error]');
  document.querySelectorAll('[data-role-choice]').forEach((button) => {
    button.addEventListener('click', async () => {
      document.querySelectorAll('[data-role-choice]').forEach((item) => { item.disabled = true; });
      if (roleChoiceError) { roleChoiceError.hidden = true; roleChoiceError.textContent = ''; }
      const csrf = document.cookie.split('; ').find((part) => part.startsWith('ora_csrf='));
      const token = csrf ? decodeURIComponent(csrf.slice('ora_csrf='.length)) : '';
      try {
        const response = await fetch('/api/portal/select-role', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
          body: JSON.stringify({ role: button.dataset.roleChoice })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || 'This workspace could not be opened.');
        window.location.assign(data.next);
      } catch (error) {
        if (roleChoiceError) { roleChoiceError.textContent = error.message; roleChoiceError.hidden = false; }
        document.querySelectorAll('[data-role-choice]').forEach((item) => { item.disabled = false; });
      }
    });
  });
  const helpDialog = document.querySelector('#dashboardHelpDialog');
  const helpButton = document.querySelector('[data-shortcuts-help]');
  const closeHelpButton = document.querySelector('[data-close-shortcuts]');
  // Shared portal rule: backdrop clicks dismiss dialogs without a visible close
  // button. Dialogs with an × control stay open until that control or Escape is used.
  // Delegation also covers dialogs added by later portal modules.
  document.addEventListener('click', (event) => {
    const dialog = event.target;
    if (!(dialog instanceof HTMLDialogElement) || !dialog.open) return;
    if (dialog.querySelector('.close-button')) return;
    dialog.close();
  });
  if (helpDialog && helpButton) {
    helpButton.addEventListener('click', () => helpDialog.showModal());
    closeHelpButton?.addEventListener('click', () => helpDialog.close());
    document.addEventListener('keydown', (event) => {
      if (event.key !== '?' || helpDialog.open || event.altKey || event.ctrlKey || event.metaKey) return;
      const target = event.target;
      if (target instanceof HTMLElement && (target.isContentEditable || /INPUT|TEXTAREA|SELECT/.test(target.tagName))) return;
      event.preventDefault();
      helpDialog.showModal();
    });
  }
  document.querySelectorAll('[data-registration-approval]').forEach((form) => {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const button = form.querySelector('button');
      button.disabled = true;
      const csrf = document.cookie.split('; ').find((part) => part.startsWith('ora_csrf='));
      const token = csrf ? decodeURIComponent(csrf.slice('ora_csrf='.length)) : '';
      try {
        const response = await fetch(form.action, {
          method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': token }
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || 'Approval could not be completed. Please try again.');
        form.closest('[data-pending-registration]')?.remove();
        const count = document.querySelector('.approval-card .count-pill');
        if (count) {
          const remaining = document.querySelectorAll('[data-pending-registration]').length;
          count.textContent = `${remaining} pending`;
        }
        toast.textContent = data.message || 'Student registration approved.';
        toast.hidden = false;
      } catch (error) {
        toast.textContent = error.message;
        toast.hidden = false;
        button.disabled = false;
      }
    });
  });
  const profileMenuButton = document.querySelector('[data-profile-menu]');
  const profileMenu = document.querySelector('#profileMenu');
  const profileDialog = document.querySelector('#profileDialog');
  const profileViewPanel = document.querySelector('[data-profile-view]');
  const profileEditPanel = document.querySelector('[data-profile-edit-panel]');
  const profileForm = document.querySelector('[data-profile-form]');
  const profileStatus = document.querySelector('[data-profile-status]');
  const profileVerificationForm = document.querySelector('[data-profile-verification-form]');
  const profileVerificationMessage = document.querySelector('[data-profile-verification-message]');
  const profileVerificationStatus = document.querySelector('[data-profile-verification-status]');
  const avatarImage = document.querySelector('[data-avatar-image]');
  const avatarInitial = document.querySelector('[data-avatar-initial]');
  const profileDialogPhoto = document.querySelector('[data-profile-dialog-photo]');
  const profileDialogInitial = document.querySelector('[data-profile-dialog-initial]');
  const avatarUploadTrigger = document.querySelector('[data-avatar-upload-trigger]');
  const avatarUploadInput = document.querySelector('[data-avatar-upload]');
  const profileAvatarPreview = document.querySelector('[data-profile-avatar-preview]');
  const avatarFileName = document.querySelector('[data-avatar-file-name]');
  const avatarFileInput = profileForm?.elements.namedItem('avatar');
  const passwordDialog = document.querySelector('#passwordDialog');
  const passwordForm = document.querySelector('[data-password-form]');
  const passwordStatus = document.querySelector('[data-password-status]');
  const csrfToken = () => {
    const cookie = document.cookie.split('; ').find((part) => part.startsWith('ora_csrf='));
    return cookie ? decodeURIComponent(cookie.slice('ora_csrf='.length)) : '';
  };
  const readJson = async (response, fallback) => {
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || fallback);
    return data;
  };
  let profilePreviewObjectUrl = '';
  let currentAvatarUrl = profileDialogPhoto?.getAttribute('src') || '';
  let pendingProfileChallengeId = '';
  const showAvatar = (url) => {
    if (!avatarImage || !avatarInitial) return;
    currentAvatarUrl = url || '';
    if (profilePreviewObjectUrl) {
      URL.revokeObjectURL(profilePreviewObjectUrl);
      profilePreviewObjectUrl = '';
    }
    if (url) {
      avatarImage.src = `${url}${url.includes('?') ? '&' : '?'}v=${Date.now()}`;
      avatarImage.hidden = false;
      avatarInitial.hidden = true;
    } else {
      avatarImage.removeAttribute('src');
      avatarImage.hidden = true;
      avatarInitial.hidden = false;
    }
    if (profileDialogPhoto) {
      if (url) {
        profileDialogPhoto.src = `${url}${url.includes('?') ? '&' : '?'}v=${Date.now()}`;
        profileDialogPhoto.hidden = false;
        if (profileDialogInitial) profileDialogInitial.hidden = true;
      } else {
        profileDialogPhoto.removeAttribute('src');
        profileDialogPhoto.hidden = true;
        if (profileDialogInitial) profileDialogInitial.hidden = false;
      }
    }
    if (profileAvatarPreview) {
      if (url) {
        profileAvatarPreview.src = `${url}${url.includes('?') ? '&' : '?'}v=${Date.now()}`;
        profileAvatarPreview.hidden = false;
      } else {
        profileAvatarPreview.removeAttribute('src');
        profileAvatarPreview.hidden = true;
      }
    }
  };
  const setProfileValues = (profile) => {
    document.dispatchEvent(new Event("portal-profile-updated"));
    for (const [key, value] of Object.entries({
      firstName: profile.firstName,
      middleName: profile.middleName,
      lastName: profile.lastName,
      email: profile.email,
      mobileNumber: profile.mobileNumber
    })) {
      const field = profileForm.elements.namedItem(key);
      if (field) {
        field.value = value || '';
        field.defaultValue = value || '';
      }
      const display = profileDialog?.querySelector(`[data-profile-value="${key}"]`);
      if (display) display.textContent = value || '—';
    }
    const displayName = [profile.firstName, profile.middleName, profile.lastName].filter(Boolean).join(' ').trim();
    const displayNameNode = profileDialog?.querySelector('[data-profile-display-name]');
    if (displayNameNode) displayNameNode.textContent = displayName || profile.username || '';
    const roleNode = profileDialog?.querySelector('[data-profile-display-role]');
    if (roleNode) roleNode.textContent = profile.role || '';
    const roleValue = profileDialog?.querySelector('[data-profile-value="role"]');
    if (roleValue) roleValue.textContent = profile.role || '';
    showAvatar(profile.avatarUrl);
  };
  const openProfileDialog = (mode = 'view') => {
    if (!profileDialog) return;
    profileMenu.hidden = true;
    profileMenuButton?.setAttribute('aria-expanded', 'false');
    profileViewPanel.hidden = mode === 'edit';
    profileEditPanel.hidden = mode !== 'edit';
    profileForm.hidden = mode !== 'edit';
    profileVerificationForm.hidden = true;
    profileStatus.hidden = true;
    profileVerificationStatus.hidden = true;
    if (mode === 'edit') {
      profileForm.reset();
      if (avatarFileInput) avatarFileInput.value = '';
      if (avatarFileName) avatarFileName.textContent = 'No photo selected';
      showAvatar(currentAvatarUrl);
    }
    if (!profileDialog.open) profileDialog.showModal();
    if (mode === 'edit') profileForm.elements.namedItem('firstName')?.focus();
  };
  document.querySelector('[data-open-profile]')?.addEventListener('click', () => openProfileDialog('view'));
  document.querySelectorAll('[data-open-profile-edit],[data-edit-profile]').forEach((button) => button.addEventListener('click', () => openProfileDialog('edit')));
  document.querySelectorAll('[data-close-profile]').forEach((button) => button.addEventListener('click', () => profileDialog.close()));
  profileDialog?.addEventListener('close', () => {
    if (pendingProfileChallengeId) {
      fetch('/api/portal/profile/verification/cancel', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ challengeId: pendingProfileChallengeId })
      }).catch(() => {});
      pendingProfileChallengeId = '';
    }
  });
  if (profileMenuButton && profileMenu) {
    profileMenuButton.addEventListener('click', () => {
      const open = profileMenu.hidden;
      profileMenu.hidden = !open;
      profileMenuButton.setAttribute('aria-expanded', String(open));
    });
    document.addEventListener('click', (event) => {
      if (event.target instanceof Element && !event.target.closest('.profile-menu')) {
        profileMenu.hidden = true;
        profileMenuButton.setAttribute('aria-expanded', 'false');
      }
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !profileMenu.hidden) {
        profileMenu.hidden = true;
        profileMenuButton.setAttribute('aria-expanded', 'false');
        profileMenuButton.focus();
      }
    });
  }
  avatarUploadTrigger?.addEventListener('click', () => {
    if (avatarUploadInput) {
      avatarUploadInput.value = '';
      avatarUploadInput.click();
    }
  });
  avatarUploadInput?.addEventListener('change', async () => {
    const file = avatarUploadInput.files?.[0];
    if (!file || !avatarUploadTrigger) return;
    avatarUploadTrigger.disabled = true;
    try {
      const formData = new FormData();
      formData.append('avatar', file);
      const result = await readJson(await fetch('/api/portal/avatar/update', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrfToken() },
        body: formData
      }), 'Profile photo could not be updated.');
      showAvatar(result.avatarUrl);
      toast.textContent = result.message || 'Profile photo updated.';
      toast.hidden = false;
    } catch (error) {
      toast.textContent = error.message || 'Profile photo could not be updated.';
      toast.hidden = false;
    } finally {
      avatarUploadInput.value = '';
      avatarUploadTrigger.disabled = false;
    }
  });
  document.querySelectorAll('[data-cancel-profile-edit]').forEach((button) => button.addEventListener('click', async () => {
    if (pendingProfileChallengeId) {
      await fetch('/api/portal/profile/verification/cancel', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ challengeId: pendingProfileChallengeId })
      }).catch(() => {});
      pendingProfileChallengeId = '';
    }
    profileForm.reset();
    profileForm.hidden = true;
    profileVerificationForm.hidden = true;
    profileEditPanel.hidden = true;
    profileViewPanel.hidden = false;
    profileStatus.hidden = true;
    profileVerificationStatus.hidden = true;
    if (avatarFileInput) avatarFileInput.value = '';
    if (avatarFileName) avatarFileName.textContent = 'No photo selected';
    showAvatar(currentAvatarUrl);
  }));
  avatarFileInput?.addEventListener('change', () => {
    const file = avatarFileInput.files?.[0];
    if (avatarFileName) avatarFileName.textContent = file?.name || 'No photo selected';
    if (!file || !profileAvatarPreview) return;
    if (profilePreviewObjectUrl) URL.revokeObjectURL(profilePreviewObjectUrl);
    profilePreviewObjectUrl = URL.createObjectURL(file);
    profileAvatarPreview.src = profilePreviewObjectUrl;
    profileAvatarPreview.hidden = false;
  });
  profileForm?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const submit = profileForm.querySelector('[type="submit"]');
    submit.disabled = true;
    profileStatus.hidden = true;
    try {
      const profile = await readJson(await fetch('/api/portal/profile/update', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrfToken() },
        body: new FormData(profileForm)
      }), 'Profile could not be updated.');
      setProfileValues(profile);
      if (avatarFileInput) avatarFileInput.value = '';
      if (avatarFileName) avatarFileName.textContent = 'No photo selected';
      if (profile.requiresVerification) {
        pendingProfileChallengeId = profile.challengeId;
        profileForm.hidden = true;
        profileVerificationForm.reset();
        profileVerificationForm.dataset.channels = profile.channels.join(',');
        profileVerificationForm.querySelector('[data-email-otp-field]').hidden = !profile.channels.includes('email');
        profileVerificationForm.querySelector('[data-mobile-otp-field]').hidden = !profile.channels.includes('mobile');
        const emailOtp = profileVerificationForm.elements.namedItem('emailOtp');
        const mobileOtp = profileVerificationForm.elements.namedItem('mobileOtp');
        emailOtp.disabled = !profile.channels.includes('email');
        emailOtp.required = profile.channels.includes('email');
        mobileOtp.disabled = !profile.channels.includes('mobile');
        mobileOtp.required = profile.channels.includes('mobile');
        profileVerificationMessage.textContent = profile.message;
        profileVerificationStatus.hidden = true;
        profileVerificationForm.hidden = false;
        profileVerificationForm.querySelector('input:not(:disabled)')?.focus();
        return;
      }
      profileEditPanel.hidden = true;
      profileViewPanel.hidden = false;
    } catch (error) {
      profileStatus.textContent = error.message;
      profileStatus.hidden = false;
    } finally {
      submit.disabled = false;
    }
  });
  profileVerificationForm?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const submit = profileVerificationForm.querySelector('[type="submit"]');
    submit.disabled = true;
    profileVerificationStatus.hidden = true;
    const payload = {
      challengeId: pendingProfileChallengeId,
      emailOtp: profileVerificationForm.elements.namedItem('emailOtp').value,
      mobileOtp: profileVerificationForm.elements.namedItem('mobileOtp').value
    };
    try {
      const profile = await readJson(await fetch('/api/portal/profile/verify', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify(payload)
      }), 'Contact verification could not be completed.');
      pendingProfileChallengeId = '';
      setProfileValues(profile);
      profileVerificationForm.hidden = true;
      profileEditPanel.hidden = true;
      profileViewPanel.hidden = false;
    } catch (error) {
      profileVerificationStatus.textContent = error.message;
      profileVerificationStatus.hidden = false;
    } finally {
      submit.disabled = false;
    }
  });
  document.querySelector('[data-open-password]')?.addEventListener('click', () => {
    profileMenu.hidden = true;
    profileMenuButton?.setAttribute('aria-expanded', 'false');
    passwordForm.reset();
    passwordStatus.hidden = true;
    passwordDialog.showModal();
    passwordForm.elements.namedItem('currentPassword')?.focus();
  });
  document.querySelectorAll('[data-close-password]').forEach((button) => button.addEventListener('click', () => passwordDialog.close()));
  passwordForm?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(passwordForm).entries());
    if (payload.newPassword !== payload.confirmPassword) {
      passwordStatus.textContent = 'The new passwords do not match.';
      passwordStatus.hidden = false;
      return;
    }
    const submit = passwordForm.querySelector('[type="submit"]');
    submit.disabled = true;
    passwordStatus.hidden = true;
    try {
      const data = await readJson(await fetch('/api/portal/password/update', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify(payload)
      }), 'Password could not be updated.');
      passwordForm.reset();
      passwordDialog.close();
      toast.textContent = data.message || 'Password updated.';
      toast.hidden = false;
    } catch (error) {
      passwordStatus.textContent = error.message;
      passwordStatus.hidden = false;
    } finally {
      submit.disabled = false;
    }
  });
  document.querySelectorAll('[data-logout]').forEach((button) => {
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        const csrf = document.cookie.split('; ').find((part) => part.startsWith('ora_csrf='));
        const token = csrf ? decodeURIComponent(csrf.slice('ora_csrf='.length)) : '';
        const response = await fetch('/api/portal/logout', {
          method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': token }
        });
        if (!response.ok) throw new Error('Sign out could not be completed. Please try again.');
        const data = await response.json();
        window.location.assign(data.next || '/portal/');
      } catch (error) {
        toast.textContent = error.message;
        toast.hidden = false;
        button.disabled = false;
      }
    });
  });
  // Keep portal presence active while the authenticated page is open, including in the background.
  if (document.body.classList.contains('dashboard-page')) {
    const portalHeartbeat = async () => {
      const csrfCookie = document.cookie.split('; ').find((part) => part.startsWith('ora_csrf='));
      const csrfToken = csrfCookie ? decodeURIComponent(csrfCookie.slice('ora_csrf='.length)) : '';
      try {
        const response = await fetch('/api/portal/activity/heartbeat', {
          method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
          body: JSON.stringify({ page: 'portal', visible: document.visibilityState === 'visible', active: Date.now() - (window.portalLastActivityAt || Date.now()) < 30000 })
        });
        if (response.status === 401) window.location.replace('/portal/');
      } catch { /* The next heartbeat will restore live presence. */ }
    };
    portalHeartbeat();
    window.setInterval(portalHeartbeat, 15000);
    document.addEventListener('visibilitychange', portalHeartbeat);
  }
})();
