/* Profile tab: CV upload, the editable profile, model settings, vacancy list, "delete my data".
 * Everything here stays on this computer.
 */
JPUI.ProfileTab = {
  props: { active: Boolean },
  emits: ["notify", "changed"],
  data() {
    return {
      cv: { has: false, chars: 0, preview: "", pasted: "", busy: false },
      analyzing: false, analyzeStart: 0, now: Date.now(),
      form: null, saving: false, hasProfile: false,
      model: { current: "", chosen: "", installed: [], num_ctx: 8192, think: false, busy: false },
      agents: { data: null, busy: false },            // teams from agents.yaml and who does which job
      vacancies: { offers: 0, age_hours: null, busy: false },
      confirmDelete: false,
      sys: { busy: false, result: null, started: 0 },
    };
  },
  computed: {
    waited() { return Math.round((this.now - this.analyzeStart) / 1000); },
    ageText() { return JP.ageLabel(this.vacancies.age_hours); },
  },
  watch: { active(now) { if (now) this.load(); } },
  mounted() {
    this.timer = setInterval(() => { this.now = Date.now(); }, 1000);
    if (this.active) this.load();
  },
  beforeUnmount() { clearInterval(this.timer); },
  methods: {
    say(kind, text) { this.$emit("notify", kind, text); },

    async load() {
      try {
        const p = await JPUI.api("/api/profile");
        this.cv = { ...this.cv, has: p.has_cv, chars: p.cv_chars, preview: p.cv_preview };
        this.hasProfile = !!p.profile;
        this.fill(p.profile);
      } catch (e) { this.say("error", "Could not load your profile: " + e.message); }
      try {
        const m = await JPUI.api("/api/model");
        this.model = { ...this.model, current: m.model, chosen: m.model, installed: m.installed || [],
                       num_ctx: m.num_ctx, think: m.think };
      } catch (e) { /* the model box simply stays empty */ }
      await this.loadAgents();
      try {
        const h = await JPUI.api("/api/health");
        this.vacancies = { ...this.vacancies, offers: h.vacancies.offers, age_hours: h.vacancies.age_hours };
      } catch (e) { /* optional */ }
    },

    fill(profile) {
      const p = profile || {};
      this.form = {
        name: p.name || "", city: p.city || "", summary: p.summary || "",
        education: JP.joinList(p.education), skills: JP.joinList(p.skills), languages: JP.joinList(p.languages),
        experience: JP.joinList(p.experience), interests: JP.joinList(p.interests),
      };
    },

    // ---- CV
    async uploadFile(event) {
      const file = event.target.files[0];
      if (!file) return;
      const data = new FormData();
      data.append("file", file);
      await this.sendCv(data);
      event.target.value = "";
    },
    async uploadPasted() {
      const data = new FormData();
      data.append("text", this.cv.pasted);
      await this.sendCv(data);
    },
    async sendCv(formData) {
      this.cv.busy = true;
      try {
        const res = await JPUI.api("/api/profile/cv", { method: "POST", body: formData });
        this.cv = { ...this.cv, has: true, chars: res.cv_chars, preview: res.cv_preview, pasted: "" };
        this.say("info", "CV saved on this computer. Now press \"Analyze my CV\".");
      } catch (e) { this.say("error", e.message); }
      finally { this.cv.busy = false; }
    },
    async analyze() {
      this.analyzing = true; this.analyzeStart = Date.now();
      try {
        const res = await JPUI.api("/api/profile/analyze", { method: "POST" });
        this.hasProfile = true;
        this.fill(res.profile);
        this.$emit("changed");
        this.say("info", "Your profile is ready. Check it and correct anything that is wrong.");
      } catch (e) { this.say("error", e.message); }
      finally { this.analyzing = false; }
    },

    // ---- profile form
    async saveProfile() {
      this.saving = true;
      const f = this.form;
      const body = {
        name: f.name.trim() || null, city: f.city.trim() || null, summary: f.summary.trim(),
        education: JP.splitList(f.education), skills: JP.splitList(f.skills), languages: JP.splitList(f.languages),
        experience: JP.splitList(f.experience), interests: JP.splitList(f.interests),
      };
      try {
        await JPUI.api("/api/profile", { method: "PUT", ...JPUI.jsonBody(body) });
        this.hasProfile = true;
        this.$emit("changed");
        this.say("info", "Profile saved.");
      } catch (e) { this.say("error", "Could not save: " + e.message); }
      finally { this.saving = false; }
    },

    // ---- agent teams: which model does which job
    async loadAgents() {
      try { this.agents.data = await JPUI.api("/api/agents"); } catch (e) { /* the panel stays empty */ }
    },
    async changeAgents(body, okText) {
      this.agents.busy = true;
      try {
        this.agents.data = await JPUI.api("/api/agents", { method: "PUT", ...JPUI.jsonBody(body) });
        this.$emit("changed");
        this.say("info", okText);
      } catch (e) { this.say("error", e.message); await this.loadAgents(); }
      finally { this.agents.busy = false; }
    },
    chooseTeam(event) { return this.changeAgents({ profile: event.target.value, clear_overrides: true },
      "Team '" + event.target.value + "' selected. The chat model loads in the background."); },
    chooseRoleModel(role, model) { return this.changeAgents({ overrides: { [role]: model || null } },
      model ? "The " + role + " now uses " + model + "." : "The " + role + " follows the team again."); },
    teamDescription() {
      const d = this.agents.data;
      const t = d && d.teams.find((x) => x.name === d.active);
      return t ? t.description : "";
    },

    // ---- model
    async applyModel() {
      this.model.busy = true;
      try {
        const res = await JPUI.api("/api/model", { method: "PUT", ...JPUI.jsonBody({
          model: this.model.chosen, num_ctx: Number(this.model.num_ctx), think: this.model.think }) });
        this.model.current = res.model;
        await this.loadAgents();
        this.$emit("changed");
        this.say("info", "Now using " + res.model + ". The first answer is slower while the model loads.");
      } catch (e) { this.say("error", e.message); }
      finally { this.model.busy = false; }
    },

    // ---- vacancy list
    async refreshVacancies() {
      this.vacancies.busy = true;
      try {
        const res = await JPUI.api("/api/refresh", { method: "POST" });
        this.vacancies = { offers: res.offers, age_hours: res.age_hours, busy: false };
        this.$emit("changed");
        this.say("info", res.offers.toLocaleString("en-US") + " vacancies downloaded.");
      } catch (e) { this.say("warn", e.message); }
      finally { this.vacancies.busy = false; }
    },

    // ---- system check (the same report as `python -m app.preflight`)
    async runCheck(withModel) {
      this.sys = { busy: true, result: null, started: Date.now() };
      try {
        this.sys.result = await JPUI.api("/api/preflight" + (withModel ? "?chat=true" : ""));
      } catch (e) { this.say("error", "The system check failed: " + e.message); }
      finally { this.sys.busy = false; }
    },

    // ---- privacy
    async deleteMyData() {
      try {
        const res = await JPUI.api("/api/my-data", { method: "DELETE" });
        this.confirmDelete = false;
        this.hasProfile = false;
        this.cv = { has: false, chars: 0, preview: "", pasted: "", busy: false };
        this.fill(null);
        this.$emit("changed");
        this.say("info", res.message);
      } catch (e) { this.say("error", e.message); }
    },
  },
  template: `
    <section class="page tabpage">
      <div class="col wide profile" v-if="form">

        <div class="panel">
          <h3>1. Your CV</h3>
          <p class="hint">Upload a PDF or a text file, or paste the text. It is stored only on this computer.</p>
          <div class="row">
            <label class="btn file">Choose a file<input type="file" accept=".pdf,.txt,.md" @change="uploadFile" hidden></label>
            <span class="meta" v-if="cv.has">CV saved ({{ cv.chars.toLocaleString('en-US') }} characters)</span>
            <span class="meta" v-else>No CV yet</span>
          </div>
          <textarea v-model="cv.pasted" rows="3" placeholder="...or paste your CV text here" aria-label="Paste CV text"></textarea>
          <div class="row">
            <button class="btn" :disabled="!cv.pasted.trim() || cv.busy" @click="uploadPasted">Save pasted text</button>
            <button class="btn primary" :disabled="!cv.has || analyzing" @click="analyze">Analyze my CV</button>
            <span class="meta" v-if="analyzing"><span class="dots"><span></span><span></span><span></span></span> Reading your CV... {{ waited }} s (about a minute on a local model)</span>
          </div>
        </div>

        <div class="panel">
          <h3>2. Your profile <span class="meta" v-if="!hasProfile">(empty until you analyze your CV - or fill it in yourself)</span></h3>
          <p class="hint">The assistant uses this to rate vacancies and write letters. Correct anything the model got wrong.</p>
          <div class="grid2">
            <label>Name<input v-model="form.name" maxlength="80"></label>
            <label>City<input v-model="form.city" maxlength="60"></label>
          </div>
          <label>About you (one or two sentences)<textarea v-model="form.summary" rows="2" maxlength="400"></textarea></label>
          <div class="grid2">
            <label>Education <small>(one per line)</small><textarea v-model="form.education" rows="3"></textarea></label>
            <label>Experience <small>(one per line)</small><textarea v-model="form.experience" rows="3"></textarea></label>
            <label>Skills <small>(one per line)</small><textarea v-model="form.skills" rows="4"></textarea></label>
            <label>Languages <small>(e.g. Dutch (native))</small><textarea v-model="form.languages" rows="4"></textarea></label>
          </div>
          <label>Interests <small>(one per line)</small><textarea v-model="form.interests" rows="2"></textarea></label>
          <div class="row"><button class="btn primary" :disabled="saving" @click="saveProfile">Save profile</button></div>
        </div>

        <div class="panel">
          <h3>3. Language models</h3>
          <p class="hint">JobPilot uses a language model in three jobs (the search, the match score and the tracker are ordinary code).
            Choose a team, or pick a model for each job. Bigger models are better but slower, and every different model has to be loaded into memory once (about a minute).</p>
          <div v-if="agents.data">
            <div class="row">
              <label>Team
                <select aria-label="Agent team" :value="agents.data.active" :disabled="agents.busy" @change="chooseTeam">
                  <option v-for="t in agents.data.teams" :key="t.name" :value="t.name">{{ t.name }}</option>
                </select>
              </label>
              <span class="meta teamdesc">{{ teamDescription() }}</span>
            </div>
            <table class="roles">
              <tr v-for="r in agents.data.roles" :key="r.id">
                <td><b>{{ r.label }}</b><br><span class="meta">{{ r.description }}</span></td>
                <td>
                  <select :aria-label="'Model for ' + r.label" :disabled="agents.busy"
                          :value="agents.data.overrides[r.id] || ''" @change="chooseRoleModel(r.id, $event.target.value)">
                    <option value="">Team choice ({{ agents.data.resolved[r.id].source === 'override' ? 'not set' : agents.data.resolved[r.id].model }})</option>
                    <option v-for="m in (agents.data.installed || [])" :key="m" :value="m">{{ m }}</option>
                  </select>
                  <div class="meta">uses <b>{{ agents.data.resolved[r.id].model }}</b>
                    <span v-if="agents.data.resolved[r.id].tools !== 'all'"> - {{ agents.data.resolved[r.id].tools.length }} tools</span></div>
                </td>
              </tr>
            </table>
            <p class="meta warn-text" v-for="p in agents.data.problems" :key="p">agents.yaml: {{ p }}</p>
          </div>
          <h4>Default model</h4>
          <p class="hint">Used by every job that is set to "default" in the team.</p>
          <div class="row">
            <label>Model
              <select aria-label="Default model" v-model="model.chosen">
                <option v-if="!model.installed.includes(model.current)" :value="model.current">{{ model.current }}</option>
                <option v-for="m in model.installed" :key="m" :value="m">{{ m }}</option>
              </select>
            </label>
            <label>Context size
              <select aria-label="Context size" v-model="model.num_ctx"><option :value="2048">2048</option><option :value="4096">4096</option><option :value="8192">8192</option><option :value="16384">16384</option></select>
            </label>
            <label class="check"><input type="checkbox" v-model="model.think"> Let the model "think" first (slower)</label>
            <button class="btn primary" :disabled="model.busy || !model.chosen" @click="applyModel">Use this model</button>
          </div>
          <p class="meta">Default model now: <b>{{ model.current }}</b></p>
        </div>

        <div class="panel">
          <h3>4. Vacancy list</h3>
          <div class="row">
            <span class="meta" v-if="vacancies.offers">{{ vacancies.offers.toLocaleString('en-US') }} Le Forem offers stored on this computer<span v-if="ageText"> ({{ ageText }})</span>.</span>
            <span class="meta" v-else>No local list yet: searches use the live job portal.</span>
            <button class="btn" :disabled="vacancies.busy" @click="refreshVacancies">{{ vacancies.busy ? 'Downloading...' : 'Refresh now' }}</button>
          </div>
        </div>

        <div class="panel">
          <h3>5. System check</h3>
          <p class="hint">Is everything ready? Checks Ollama, the models, the data and the files. Run it before a presentation.</p>
          <div class="row">
            <button class="btn" :disabled="sys.busy" @click="runCheck(false)">Quick check</button>
            <button class="btn" :disabled="sys.busy" @click="runCheck(true)" title="Also loads the model and asks it one question">Check with a model test</button>
            <span class="meta" v-if="sys.busy"><span class="dots"><span></span><span></span><span></span></span> Checking... {{ Math.round((now - sys.started) / 1000) }} s</span>
          </div>
          <div v-if="sys.result">
            <p class="verdict" :class="sys.result.ready ? (sys.result.counts.warn ? 'warn' : 'ok') : 'bad'"><b>{{ sys.result.verdict }}</b>
              <span class="meta"> - {{ sys.result.counts.ok }} ok, {{ sys.result.counts.warn }} warnings, {{ sys.result.counts.fail }} failed</span></p>
            <ul class="checks">
              <li v-for="c in sys.result.checks" :key="c.name" :class="c.status">
                <span class="mark">{{ {ok: 'OK', warn: '!', fail: 'X', skip: '-'}[c.status] }}</span>
                <span><b>{{ c.name }}</b> {{ c.detail }}<span class="fix" v-if="c.fix && (c.status === 'warn' || c.status === 'fail')"><br>Fix: <code>{{ c.fix }}</code></span></span>
              </li>
            </ul>
          </div>
        </div>

        <div class="panel danger">
          <h3>6. Privacy</h3>
          <p class="hint">Everything is stored in one file on this computer. Nothing is sent anywhere except search words to the job sources.</p>
          <div class="row" v-if="!confirmDelete"><button class="btn" @click="confirmDelete = true">Delete my data...</button></div>
          <div class="row" v-else>
            <span>Delete your CV, profile and saved applications for good?</span>
            <button class="btn danger" @click="deleteMyData">Yes, delete</button>
            <button class="btn" @click="confirmDelete = false">Cancel</button>
          </div>
        </div>
      </div>
    </section>`,
};
