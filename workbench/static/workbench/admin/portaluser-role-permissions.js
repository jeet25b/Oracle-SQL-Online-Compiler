(function () {
  function initialize() {
  const roleSelect = document.getElementById('id_role');
  const approvalPermission = document.getElementById('id_can_approve_students');
  const allActivityPermission = document.getElementById('id_can_view_all_compiler_activity');
  const permissionFields = [approvalPermission, allActivityPermission].filter(Boolean);
  if (!permissionFields.length) return;

  const additionalTeacherRole = () => Array.from(
    document.querySelectorAll('input[name="additional_roles"]')
  ).some((input) => input.value === 'TEACHER' && input.checked);

  const syncApprovalPermission = () => {
    const primaryTeacherRole = roleSelect && roleSelect.value === 'TEACHER';
    const teacherRoleAssigned = primaryTeacherRole || additionalTeacherRole();
    permissionFields.forEach((field) => {
      field.disabled = !teacherRoleAssigned;
      if (!teacherRoleAssigned) field.checked = false;
    });
  };

  roleSelect?.addEventListener('change', syncApprovalPermission);
  document.querySelectorAll('input[name="additional_roles"]').forEach((input) => {
    input.addEventListener('change', syncApprovalPermission);
  });
  syncApprovalPermission();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize);
  else initialize();
})();
