/* JobPilot web UI (Vue 3, no build step). Talks to the FastAPI server in app/main.py.
 *
 * The chat reads /api/chat as a stream of Server-Sent Events and shows, live:
 *   tool_call -> a chip "search_jobs running..."     tool_result -> chip turns green/red
 *   token     -> the answer, word by word             retract     -> an invented answer was thrown away
 *   error     -> a calm message                       done        -> timing information
 * Job cards, match scores and cover letters are built from the TOOL RESULTS (real data),
 * not from the model's text - so they are always correct even if the text is short.
 */
const { createApp, reactive, ref, computed, nextTick, onMounted, onBeforeUnmount } = Vue;

const app = createApp({
  setup() {
    // ------------------------------------------------------------------ state
    const tabs = [
      { id: "chat", label: "Chat" }, { id: "jobs", label: "Jobs" },
      { id: "tracker", label: "Tracker" }, { id: "profile", label: "Profile" },
    ];
    const tab = ref("chat");
    const health = ref(null);
    const serverDown = ref(false);
    const messages = reactive([]);
    const input = ref("");
    const busy = ref(false);
    const saved = reactive(new Set());
    const toasts = reactive([]);                  // short-lived confirmations and problems
    const now = ref(Date.now());
    const scroller = ref(null);
    const box = ref(null);
    let controller = null;
    let nextId = 1;
    let firstSend = true;                         // the server may remember an older chat: start clean
    let timers = [];

    const demo = ref([]);                          // scripted scenarios for the presentation (+ recordings)
    const showDemo = ref(false);

    const suggestions = [
      "Find student jobs in Antwerp",
      "Zoek een stage in Leuven",
      "Chercher un job étudiant à Liège",
      "Which jobs fit my CV best?",
    ];

    // ---------------------------------------------------------------- helpers
    const render = JP.renderMarkdown;
    const fmt = JP.formatMs;
    const sourceText = JP.sourceLabel;
    const tabLabel = computed(() => (tabs.find((t) => t.id === tab.value) || {}).label);

    // Banners (top of the page) describe a STATE that lasts: server down, Ollama off, list downloading.
    const dismissed = new Set();
    function dismiss(id) { dismissed.add(id); }

    const api = JPUI.api;                          // fetch + friendly errors (see components.js)

    // Toasts (floating, bottom-right) confirm an ACTION: "Profile saved", "Could not save: ...".
    // They fade away by themselves, so they never pile up and never push the page around.
    function notify(kind, text) {
      const id = nextId++;
      toasts.push({ id, kind, text });
      setTimeout(() => closeToast(id), kind === "info" ? 4500 : 9000);
    }
    function closeToast(id) {
      const i = toasts.findIndex((t) => t.id === id);
      if (i >= 0) toasts.splice(i, 1);
    }

    // ----------------------------------------------------------------- health
    async function loadHealth() {
      try {
        health.value = await api("/api/health");
        serverDown.value = false;
      } catch (e) {
        serverDown.value = true;
      }
    }
    const ollamaOk = computed(() => !!(health.value && health.value.ollama && health.value.ollama.ok));
    const hasProfile = computed(() => !!(health.value && health.value.database && health.value.database.has_profile));
    const onProfileChanged = () => loadHealth();            // a new CV/profile/model changes the status bar
    const onTrackerChanged = () => loadSaved();             // a removed application is no longer "Saved"
    const modelName = computed(() => (health.value && health.value.models.chat.name) || "");
    // While the server loads the model and reads the system prompt the first answer would be slow.
    const warmText = computed(() => {
      const w = health.value && health.value.warm;
      return w && w.status === "warming" ? " · loading..." : "";
    });
    const modelTitle = computed(() => {
      const h = health.value;
      if (!h) return "";
      const v = h.ollama && h.ollama.version ? "Ollama " + h.ollama.version + ". " : "";
      return v + ((h.warm && h.warm.message) || "");
    });
    const vacanciesLabel = computed(() => {
      const v = health.value && health.value.vacancies;
      if (!v || !v.offers) return "Vacancies: live search";
      return v.offers.toLocaleString("en-US") + " offers · " + JP.ageLabel(v.age_hours);
    });
    const vacanciesTitle = computed(() => {
      const v = health.value && health.value.vacancies;
      return v && v.message ? v.message : "Source: Le Forem open data (CC BY-SA 4.0) and Adzuna";
    });
    // Banners that follow the server state (they disappear by themselves when the problem is gone).
    const allBanners = computed(() => {
      const list = [];
      if (serverDown.value) {
        list.push({ id: "down", kind: "error", dismissable: false,
          text: "Cannot reach the JobPilot server. Check that it is still running (python -m app.main)." });
      }
      const h = health.value;
      if (h && !h.ollama.ok) {
        list.push({ id: "ollama", kind: "warn", dismissable: false,
          text: h.ollama.message || "Ollama is not running. Start the Ollama app to use the chat." });
      } else if (h && h.models.chat.installed === false) {
        list.push({ id: "model", kind: "warn", dismissable: false,
          text: "The model '" + h.models.chat.name + "' is not installed. Install it with: ollama pull " + h.models.chat.name });
      }
      if (h && h.vacancies && !h.vacancies.offers && !dismissed.has("nolist")) {
        list.push({ id: "nolist", kind: "info", dismissable: true,
          text: "The local vacancy list is still downloading. Until then searches use the live job portal." });
      }
      return list;
    });

    // ------------------------------------------------------------------ chat
    function newAssistant() {
      return reactive({
        id: nextId++, role: "assistant", text: "", steps: [], jobs: {}, matches: {}, letters: [],
        source: null, attribution: [], notes: [], error: null, notice: null, stats: null,
        waiting: true, startedAt: Date.now(), replay: null,
      });
    }

    function handleEvent(m, type, data) {
      if (type === "token") {
        m.waiting = false;
        m.text += data.text;
      } else if (type === "tool_call") {
        m.waiting = false;
        m.steps.push({ id: nextId++, name: data.name, status: "running", ms: null, summary: "",
          argsText: JP.summarizeArgs(data.arguments), startedAt: Date.now() });
      } else if (type === "tool_result") {
        const step = m.steps.slice().reverse().find((s) => s.name === data.name && s.status === "running");
        if (step) {
          step.status = data.ok ? "ok" : "failed";
          step.ms = data.duration_ms;
          step.summary = JP.summarizeResult(data.name, data.ok, data.data, data.error);
        }
        if (data.ok && data.data) harvest(m, data.data);
        m.waiting = true;                           // the model now reads the result
      } else if (type === "retract") {
        m.text = "";
        m.notice = (data.reason || "The first answer could not be verified.") + " The first answer was discarded; trying again...";
        m.waiting = true;
      } else if (type === "error") {
        m.error = data.message;
        m.waiting = false;
      } else if (type === "replay") {
        m.replay = data;                            // a recorded REAL run is being played back: say so
      } else if (type === "done") {
        m.stats = data.stats;
        m.waiting = false;
        if (!m.text && data.text) m.text = data.text;
      }
    }

    // Take the useful parts out of a tool result: vacancies, scores, letters, data source.
    function harvest(m, d) {
      (d.jobs || []).forEach((j) => { m.jobs[j.id] = j; });
      (d.matches || []).forEach((x) => { m.matches[x.job_id] = x; });
      if (d.letter) m.letters.push({ text: d.letter, words: d.word_count, job: (d.job && d.job.title) || "" });
      if (d.data_source) m.source = { kind: d.data_source, date: d.snapshot_date };
      (d.attribution || []).forEach((a) => { if (!m.attribution.some((x) => x.text === a.text)) m.attribution.push(a); });
      (d.notes || []).forEach((n) => { if (!m.notes.includes(n)) m.notes.push(n); });
    }

    function cardsOf(m) {
      const cards = Object.values(m.jobs).map((j) => ({ ...j, match: m.matches[j.id] || null }));
      Object.values(m.matches).forEach((x) => {          // scored vacancies from an earlier search
        if (!m.jobs[x.job_id]) cards.push({ id: x.job_id, title: x.title, match: x });
      });
      const scored = cards.some((c) => c.match);
      return scored ? cards.sort((a, b) => (b.match ? b.match.score : -1) - (a.match ? a.match.score : -1)) : cards;
    }

    function waitingText(m) {
      const s = Math.round((now.value - m.startedAt) / 1000);
      const last = m.steps[m.steps.length - 1];
      const what = last && last.status !== "running" ? "Reading the " + last.name + " result" : "Thinking";
      return what + "... " + s + " s" + (s > 20 ? " (a local model can take a minute or two)" : "");
    }
    const speed = (m) => {
      const s = m.stats;
      return s && s.eval_duration ? (s.eval_count / (s.eval_duration / 1e9)).toFixed(1) : "";
    };

    async function send(text, options) {
      options = options || {};
      text = (text !== undefined ? text : input.value).trim();
      if (!text || busy.value) return;
      input.value = "";
      nextTick(grow);
      tab.value = "chat";
      busy.value = true;
      showDemo.value = false;
      messages.push({ id: nextId++, role: "user", text });
      const m = newAssistant();
      messages.push(m);
      scrollDown(true);
      controller = new AbortController();
      try {
        const res = await fetch("/api/chat", {
          method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
          body: JSON.stringify({ message: text, reset: firstSend, replay: options.replay || null }),
        });
        firstSend = false;
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.error || "The server answered " + res.status);
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        const feed = JP.createSSEParser((type, data) => { handleEvent(m, type, data); scrollDown(); });
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          feed(decoder.decode(value, { stream: true }));
        }
      } catch (e) {
        m.error = e.name === "AbortError" ? "Stopped. (The server may still finish the last step.)"
          : e instanceof TypeError ? "Lost the connection to the JobPilot server."
          : e.message;
      } finally {
        m.waiting = false;
        busy.value = false;
        controller = null;
        loadHealth();
        scrollDown();
      }
    }

    function stop() { if (controller) controller.abort(); }
    async function newChat() {
      if (busy.value) return;
      messages.splice(0);
      firstSend = true;
      try { await api("/api/chat/reset", { method: "POST" }); } catch (e) { /* the next send resets anyway */ }
    }

    async function saveJob(job) {
      try {
        await api("/api/applications", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ job_id: job.id }) });
        saved.add(job.id);
      } catch (e) {
        notify("error", "Could not save the vacancy: " + e.message);
      }
    }
    async function loadDemo() {
      try { demo.value = (await api("/api/demo")).scenarios; } catch (e) { /* the demo panel is optional */ }
    }
    const replayDate = (r) => JP.shortDate(r && r.recorded_at);

    async function loadSaved() {
      try {
        const list = (await api("/api/applications")).applications;
        saved.clear();
        list.forEach((a) => saved.add(a.job_id));
      } catch (e) { /* optional */ }
    }
    async function copy(text) {
      try { await navigator.clipboard.writeText(text); notify("info", "Letter copied."); }
      catch (e) { notify("warn", "Copy is not allowed here: select the text and copy it by hand."); }
    }

    // ------------------------------------------------------------------ misc UI
    let stick = true;
    function scrollDown(force) {
      nextTick(() => {
        const el = scroller.value;
        if (!el) return;
        if (force || el.scrollHeight - el.scrollTop - el.clientHeight < 160) el.scrollTop = el.scrollHeight;
      });
    }
    function grow() {
      const el = box.value;
      if (!el) return;
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 144) + "px";
      el.style.overflowY = el.scrollHeight > 144 ? "auto" : "hidden";   // no scrollbar until it is needed
    }

    onMounted(() => {
      loadHealth();
      loadSaved();
      loadDemo();
      timers.push(setInterval(loadHealth, 20000));
      timers.push(setInterval(() => { now.value = Date.now(); }, 1000));
      if (box.value) box.value.focus();
    });
    onBeforeUnmount(() => timers.forEach(clearInterval));

    return {
      tabs, tab, tabLabel, health, messages, input, busy, saved, suggestions, scroller, box,
      ollamaOk, hasProfile, modelName, warmText, modelTitle, vacanciesLabel, vacanciesTitle, allBanners,
      render, fmt, sourceText, cardsOf, waitingText, speed,
      demo, showDemo, replayDate, toasts, closeToast, send, stop, newChat, saveJob, copy, dismiss, grow, notify, onProfileChanged, onTrackerChanged,
    };
  },
});

// the building blocks defined in components.js and tab-*.js
app.component("job-card", JPUI.JobCard);
app.component("jobs-tab", JPUI.JobsTab);
app.component("tracker-tab", JPUI.TrackerTab);
app.component("profile-tab", JPUI.ProfileTab);
app.mount("#app");
