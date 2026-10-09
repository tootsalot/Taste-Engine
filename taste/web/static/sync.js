// Polls the sync status once a second and updates the log until the sync finishes.
(function () {
  var log = document.getElementById("sync-log");
  var state = document.getElementById("sync-state");
  if (!log || log.dataset.running !== "true") return;

  function poll() {
    fetch(log.dataset.statusUrl, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (job) {
        log.textContent = (job.messages || []).join("\n");
        log.scrollTop = log.scrollHeight;
        if (job.running) {
          setTimeout(poll, 1000);
        } else {
          state.textContent = job.ok ? "Finished." : "Finished with problems. See the messages above.";
        }
      })
      .catch(function () { setTimeout(poll, 3000); });
  }
  poll();
})();
