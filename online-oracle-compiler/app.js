(() => {
  const form = document.querySelector('#loginForm');
  const status = document.querySelector('#formStatus');
  const userInput = document.querySelector('#userId');
  const submitText = document.querySelector('#submitText');
  const password = document.querySelector('#password');
  const togglePassword = document.querySelector('#togglePassword');
  const registerDialog = document.querySelector('#registerDialog');
  const registrationSuccessDialog = document.querySelector('#registrationSuccessDialog');
  const registrationSuccessMessage = document.querySelector('#registrationSuccessMessage');
  const registerForm = document.querySelector('#registerForm');
  const registerRole = document.querySelector('#registerRole');
  const registrationOtpForm = document.querySelector('#registrationOtpForm');
  const registrationOtpStatus = document.querySelector('#registrationOtpStatus');
  const registerStatus = document.querySelector('#registerStatus');
  const registerSubmit = document.querySelector('#registerSubmit');
  let customRegistrationFields = [];
  let registrationChallengeId = '';
  document.addEventListener('keydown', (event) => {
    if (!event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === 'Enter' && !document.querySelector('dialog[open]')) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  togglePassword.addEventListener('click', () => {
    const reveal = password.type === 'password';
    password.type = reveal ? 'text' : 'password';
    togglePassword.textContent = reveal ? 'Hide' : 'Show';
    togglePassword.setAttribute('aria-label', `${reveal ? 'Hide' : 'Show'} password`);
    togglePassword.setAttribute('aria-pressed', String(reveal));
    password.focus({ preventScroll: true });
  });

  function csrfToken() {
    const cookie = document.cookie.split('; ').find((part) => part.startsWith('ora_csrf='));
    return cookie ? decodeURIComponent(cookie.slice('ora_csrf='.length)) : '';
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!userInput.value.trim()) { userInput.focus(); return; }
    if (!password.value) { password.focus(); return; }
    const submitButton = document.querySelector('#submitButton');
    submitButton.disabled = true;
    submitText.textContent = 'Signing in…';
    status.hidden = true;
    try {
      const response = await fetch('/api/portal/login', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ username: userInput.value.trim(), password: password.value, remember: document.querySelector('#rememberMe').checked })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Sign in could not be completed. Please try again.');
      window.location.assign(data.next || '/portal/');
    } catch (error) {
      status.textContent = error instanceof TypeError
        ? 'The portal could not be reached. Check your connection and try again.'
        : (error.message || 'Sign in could not be completed. Please try again.');
      status.hidden = false;
      submitButton.disabled = false;
      submitText.textContent = 'Sign in';
    }
  });

  const resetDialog = document.querySelector('#passwordResetDialog');
  const resetForm = document.querySelector('#passwordResetForm');
  const resetChannel = document.querySelector('#resetChannel');
  const resetStatus = document.querySelector('#passwordResetStatus');
  const resetSubmit = document.querySelector('#resetSubmit');
  const resetVerifyFields = document.querySelector('#resetVerifyFields');
  const resetChangeMethod = document.querySelector('#resetChangeMethod');
  let resetStep = 'send';
  let resetBusy = false;
  async function resetRequest(action, payload) {
    const response = await fetch(`/api/portal/password-reset/${action}`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Password reset could not be completed.');
    return data;
  }
  function resetStage(step) {
    resetStep = step;
    resetVerifyFields.hidden = step !== 'complete';
    resetVerifyFields.querySelectorAll('input').forEach((input) => {
      input.disabled = step !== 'complete';
      input.value = '';
    });
    resetChannel.disabled = step === 'complete';
    resetChangeMethod.hidden = step !== 'complete';
    resetSubmit.textContent = step === 'complete' ? 'Reset password' : 'Send verification code';
  }
  document.querySelector('#forgotLink').addEventListener('click', async (event) => {
    event.preventDefault();
    if (resetBusy) return;
    if (!userInput.value.trim()) {
      status.textContent = 'Enter your account ID, email, or mobile number first.';
      status.hidden = false;
      userInput.focus();
      return;
    }
    resetBusy = true;
    try {
      const data = await resetRequest('options', { username: userInput.value.trim() });
      if (!data.channels.length) {
        status.textContent = data.message;
        status.hidden = false;
        return;
      }
      resetForm.reset();
      resetStage('send');
      resetStatus.hidden = true;
      resetChannel.replaceChildren();
      data.channels.forEach((channel) => {
        const option = document.createElement('option');
        option.value = channel.value;
        option.textContent = channel.label;
        resetChannel.append(option);
      });
      document.querySelector('#resetChannelChoice').hidden = data.channels.length === 1;
      const single = document.querySelector('#resetSingleChannel');
      single.hidden = data.channels.length !== 1;
      single.textContent = data.channels.length === 1 ? `Send code to ${data.channels[0].label}` : '';
      status.hidden = true;
      resetDialog.showModal();
    } catch (error) {
      status.textContent = error.message;
      status.hidden = false;
    } finally { resetBusy = false; }
  });
  document.querySelector('#closePasswordReset').addEventListener('click', () => {
    if (!resetBusy) resetDialog.close();
  });
  resetDialog.addEventListener('cancel', (event) => { if (resetBusy) event.preventDefault(); });
  resetChangeMethod.addEventListener('click', () => {
    if (resetBusy) return;
    resetStage('send');
    resetStatus.hidden = true;
  });
  resetForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (resetBusy) return;
    resetBusy = true;
    resetSubmit.disabled = true;
    resetChangeMethod.disabled = true;
    resetStatus.hidden = true;
    try {
      const payload = resetStep === 'send' ? { channel: resetChannel.value } : {
        code: document.querySelector('#resetCode').value.trim(),
        password: document.querySelector('#resetNewPassword').value,
        confirmPassword: document.querySelector('#resetConfirmPassword').value
      };
      const data = await resetRequest(resetStep, payload);
      if (resetStep === 'send') {
        resetStage('complete');
        resetStatus.textContent = data.message;
        resetStatus.hidden = false;
        document.querySelector('#resetCode').focus();
      } else {
        resetDialog.close();
        resetForm.reset();
        password.value = '';
        status.textContent = data.message;
        status.hidden = false;
        password.focus();
      }
    } catch (error) {
      resetStatus.textContent = error.message;
      resetStatus.hidden = false;
    } finally {
      resetBusy = false;
      resetSubmit.disabled = false;
      resetChangeMethod.disabled = false;
    }
  });
  function openRegistration(event, role) {
    event.preventDefault();
    registerForm.reset();
    registerRole.value = role;
    document.querySelector('#registerTitle').textContent = role === 'teacher' ? 'Register as a teacher' : 'Create your student account';
    document.querySelector('#registerIntro').textContent = role === 'teacher'
      ? 'Teacher registrations are reviewed by an administrator. You can sign in after approval.'
      : 'Student registrations are reviewed by an administrator. You can sign in after approval.';
    registerStatus.hidden = true;
    registrationChallengeId = '';
    registerForm.hidden = false;
    registrationOtpForm.hidden = true;
    registrationOtpStatus.hidden = true;
    registerDialog.showModal();
    document.querySelector('#registerFirstName').focus();
    syncRegistrationRequirements(role);
  }
  document.querySelector('#accessLink').addEventListener('click', (event) => openRegistration(event, 'student'));
  document.querySelector('#closeRegister').addEventListener('click', () => registerDialog.close());
  document.querySelector('#cancelRegister').addEventListener('click', () => registerDialog.close());
  document.querySelector('#cancelRegistrationOtp').addEventListener('click', async () => {
    if (registrationChallengeId) {
      fetch('/api/portal/registration/cancel', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ challengeId: registrationChallengeId })
      }).catch(() => {});
    }
    registrationChallengeId = '';
    registrationOtpForm.reset();
    registrationOtpForm.hidden = true;
    registerForm.hidden = false;
    registrationOtpStatus.hidden = true;
    document.querySelector('#registerFirstName').focus();
  });
  document.querySelector('#closeRegistrationSuccess').addEventListener('click', () => registrationSuccessDialog.close());
  document.querySelector('#continueAfterRegistration').addEventListener('click', () => registrationSuccessDialog.close());
  document.querySelectorAll('dialog').forEach((dialog) => {
    if (dialog.querySelector('.close-button')) return;
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) dialog.close();
    });
  });
  registerForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    registerStatus.classList.remove('registration-success');
    const values = Object.fromEntries(new FormData(registerForm).entries());
    values.customFields = Object.fromEntries(customRegistrationFields.map((field) => [field.key, values[field.key] || '']));
    customRegistrationFields.forEach((field) => { delete values[field.key]; });
    if (values.password !== values.confirmPassword) {
      registerStatus.textContent = 'The passwords do not match.';
      registerStatus.hidden = false;
      document.querySelector('#registerConfirmPassword').focus();
      return;
    }
    registerSubmit.disabled = true;
    registerSubmit.querySelector('span').textContent = 'Submitting…';
    registerStatus.hidden = true;
    try {
      const response = await fetch('/api/portal/register', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify(values)
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Registration could not be completed. Please try again.');
      if (data.requiresVerification) {
        registrationChallengeId = data.challengeId;
        document.querySelector('#emailOtpGroup').hidden = !data.channels.includes('email');
        document.querySelector('#mobileOtpGroup').hidden = !data.channels.includes('mobile');
        document.querySelector('#registrationOtpSpamHint').hidden = !data.channels.includes('email');
        document.querySelector('#registrationOtpIntro').textContent = data.message;
        registerForm.hidden = true;
        registrationOtpForm.hidden = false;
        document.querySelector(data.channels.includes('email') ? '#registrationEmailOtp' : '#registrationMobileOtp').focus();
        return;
      }
      registerForm.reset();
      registerDialog.close();
      registrationSuccessMessage.textContent = data.message || 'Registration submitted for review.';
      registrationSuccessDialog.showModal();
    } catch (error) {
      registerStatus.textContent = error instanceof TypeError ? 'The portal could not be reached. Check your connection and try again.' : error.message;
      registerStatus.hidden = false;
      registerStatus.classList.remove('registration-success');
    } finally {
      registerSubmit.disabled = false;
      registerSubmit.querySelector('span').textContent = 'Create account';
    }
  });
  registrationOtpForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const verifyButton = document.querySelector('#verifyRegistrationOtp');
    const buttonLabel = verifyButton.querySelector('span');
    verifyButton.disabled = true;
    buttonLabel.textContent = 'Verifying…';
    registrationOtpStatus.hidden = true;
    try {
      const response = await fetch('/api/portal/registration/verify', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({
          challengeId: registrationChallengeId,
          emailOtp: document.querySelector('#registrationEmailOtp').value.trim(),
          mobileOtp: document.querySelector('#registrationMobileOtp').value.trim(),
        })
      });
      const responseText = await response.text();
      let data = {};
      try { data = responseText ? JSON.parse(responseText) : {}; } catch { /* Use the HTTP status below for non-JSON server errors. */ }
      if (!response.ok) {
        const fallback = response.status >= 500
          ? `The server encountered an error (${response.status}) while confirming the code. Please try again; if it continues, contact the portal administrator.`
          : `The verification code could not be confirmed (HTTP ${response.status}).`;
        throw new Error(data.error || fallback);
      }
      registrationOtpForm.reset();
      registerForm.reset();
      registerDialog.close();
      registrationSuccessMessage.textContent = data.message || 'Registration submitted for review.';
      registrationSuccessDialog.showModal();
      registrationChallengeId = '';
    } catch (error) {
      registrationOtpStatus.textContent = error instanceof TypeError ? 'The portal could not be reached. Check your connection and try again.' : error.message;
      registrationOtpStatus.hidden = false;
    } finally {
      verifyButton.disabled = false;
      buttonLabel.textContent = 'Verify and submit';
    }
  });
  document.querySelector('#resendRegistrationOtp').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    if (!registrationChallengeId || button.disabled) return;
    button.disabled = true;
    registrationOtpStatus.hidden = true;
    try {
      const response = await fetch('/api/portal/registration/resend', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ challengeId: registrationChallengeId })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'A new verification code could not be sent.');
      registrationOtpStatus.textContent = data.message || 'A new verification code has been sent.';
      registrationOtpStatus.hidden = false;
      const activeOtpGroup = document.querySelector(!document.querySelector('#emailOtpGroup').hidden ? '#emailOtpGroup' : '#mobileOtpGroup');
      activeOtpGroup?.querySelector('input')?.focus();
    } catch (error) {
      registrationOtpStatus.textContent = error instanceof TypeError ? 'The portal could not be reached. Check your connection and try again.' : error.message;
      registrationOtpStatus.hidden = false;
    } finally { button.disabled = false; }
  });
  async function syncRegistrationRequirements(role = registerRole.value) {
    try {
      const response = await fetch(`/api/portal/registration-config?role=${encodeURIComponent(role)}`, { credentials: 'same-origin' });
      if (!response.ok) return;
      const data = await response.json();
      const required = new Set(data.requiredFields || []);
      document.querySelectorAll('[data-registration-label]').forEach((label) => {
        const field = label.dataset.registrationLabel;
        const input = document.getElementById(label.htmlFor);
        const isRequired = required.has(field);
        if (input) input.required = isRequired;
        label.textContent = `${label.dataset.baseLabel}${isRequired ? ' *' : ' (optional)'}`;
      });
      customRegistrationFields = Array.isArray(data.customFields) ? data.customFields : [];
      renderCustomRegistrationFields();
    } catch { /* Server-side validation still enforces the configured fields. */ }
  }
  function renderCustomRegistrationFields() {
    const container = document.querySelector('#customRegistrationFields');
    container.replaceChildren();
    container.className = 'custom-registration-fields';
    customRegistrationFields.forEach((field) => {
      if (!field || typeof field.key !== 'string' || typeof field.label !== 'string') return;
      const fieldId = `registration-${field.key}`;
      const group = document.createElement('div');
      const label = document.createElement('label');
      label.className = 'field-label';
      label.htmlFor = fieldId;
      label.textContent = `${field.label}${field.required ? ' *' : ' (optional)'}`;
      const wrap = document.createElement('div');
      wrap.className = 'input-wrap';
      const input = document.createElement(field.input_type === 'textarea' ? 'textarea' : 'input');
      input.id = fieldId;
      input.name = field.key;
      input.maxLength = 2000;
      input.required = Boolean(field.required);
      if (field.input_type === 'textarea') input.rows = 3;
      else input.type = ['text', 'email', 'tel', 'number', 'date'].includes(field.input_type) ? field.input_type : 'text';
      wrap.append(input);
      group.append(label, wrap);
      container.append(group);
    });
  }
  document.querySelector('#year').textContent = new Date().getFullYear();
})();
