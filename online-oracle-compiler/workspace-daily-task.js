/* Daily tasks always open the isolated assessment sandbox. */
window.WorkspaceDailyTask = {
  attach({isStudent}) {
    const id = new URLSearchParams(location.search).get('dailyTask');
    if (isStudent && /^\d+$/.test(id || '')) {
      location.replace(`/portal/sandbox/daily_task/${id}/`);
    }
  }
};
