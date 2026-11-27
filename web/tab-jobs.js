/* Jobs tab: search the vacancies with filters. No LLM needed (only the optional match score
 * uses the embedding model), so this tab keeps working when Ollama is down.
 */
JPUI.JobsTab = {
  props: { active: Boolean, saved: Object, hasProfile: Boolean },
  emits: ["notify", "save"],
  components: { "job-card": JPUI.JobCard },
  data() {
    return {
      f: { keywords: "", job_type: "", region: "", city: "", language: "", limit: 20, match: false },
      result: null, loading: false, started: 0, now: Date.now(), searched: false,
      letter: null,                                   // the letter dialog: {job, language, tone, loading, text, ...}
    };
  },
  computed: {
    jobs() {
      const list = this.result ? this.result.jobs.slice() : [];
      if (list.some((j) => j.match)) list.sort((a, b) => (b.match ? b.match.score : -1) - (a.match ? a.match.score : -1));
      return list;
    },
    badge() { return this.result ? JP.sourceLabel({ kind: this.result.data_source, date: this.result.snapshot_date }) : ""; },
    waited() { return Math.round((this.now - this.started) / 1000); },
  },
  watch: {
    active(now) { if (now && !this.searched) this.search(); },      // first visit: show the newest vacancies
    hasProfile(has) { if (!has) this.f.match = false; },
  },
  mounted() {
    this.timer = setInterval(() => { this.now = Date.now(); }, 1000);
    if (this.active) this.search();
  },
  beforeUnmount() { clearInterval(this.timer); },
  methods: {
    async search() {
      this.searched = true;
      this.loading = true;
      this.started = Date.now();
      const params = new URLSearchParams();
      Object.entries(this.f).forEach(([k, v]) => { if (v !== "" && v !== false && v != null) params.set(k, v); });
      try {
        this.result = await JPUI.api("/api/jobs?" + params.toString());
      } catch (e) {
        this.$emit("notify", "error", "Search failed: " + e.message);
      } finally {
        this.loading = false;
      }
    },
    reset() {
      this.f = { keywords: "", job_type: "", region: "", city: "", language: "", limit: 20, match: false };
      this.search();
    },
    openLetter(job) {
      this.letter = { job, language: "", tone: "friendly", loading: false, text: "", words: 0, error: "", started: 0 };
    },
    async writeLetter() {
      const L = this.letter;
      L.loading = true; L.error = ""; L.text = ""; L.started = Date.now();
      try {
        const args = { job_id: L.job.id, tone: L.tone };
        if (L.language) args.language = L.language;
        const res = await JPUI.api("/api/tools/draft_cover_letter", { method: "POST", ...JPUI.jsonBody(args) });
        if (!res.ok) throw new Error(res.error);
        L.text = res.data.letter; L.words = res.data.word_count; L.usedLanguage = res.data.language;
      } catch (e) {
        L.error = e.message;
      } finally {
        L.loading = false;
      }
    },
    async copyLetter() {
      try { await navigator.clipboard.writeText(this.letter.text); this.$emit("notify", "info", "Letter copied."); }
      catch (e) { this.$emit("notify", "warn", "Copy is not allowed here: select the text and copy it by hand."); }
    },
  },
  template: `
    <section class="page tabpage">
      <div class="col wide">
        <form class="filters" @submit.prevent="search">
          <input class="grow" v-model.trim="f.keywords" maxlength="100" placeholder="Keywords, e.g. barista, developer, marketing" aria-label="Keywords">
          <select v-model="f.job_type" aria-label="Type of work">
            <option value="">Any type</option><option value="student">Student job</option><option value="part-time">Part-time</option>
            <option value="internship">Internship</option><option value="full-time">Full-time</option>
          </select>
          <select v-model="f.region" aria-label="Region">
            <option value="">All regions</option><option value="flanders">Flanders</option>
            <option value="wallonia">Wallonia</option><option value="brussels">Brussels</option>
          </select>
          <input v-model.trim="f.city" maxlength="60" placeholder="City, e.g. Antwerp" aria-label="City">
          <select v-model="f.language" aria-label="Language of the ad">
            <option value="">Any language</option><option value="nl">Dutch</option><option value="fr">French</option><option value="en">English</option>
          </select>
          <select v-model.number="f.limit" aria-label="How many"><option :value="20">20 results</option><option :value="50">50 results</option></select>
          <label class="check" :title="hasProfile ? 'Score every vacancy against your CV' : 'Upload your CV in the Profile tab first'">
            <input type="checkbox" v-model="f.match" :disabled="!hasProfile"> Match with my CV
          </label>
          <button class="btn primary" :disabled="loading">Search</button>
          <button type="button" class="btn" @click="reset" :disabled="loading">Reset</button>
        </form>

        <div class="resultbar" v-if="result && !loading">
          <b>{{ result.count }}</b> {{ result.count === 1 ? 'vacancy' : 'vacancies' }}
          <span class="badge" :class="result.data_source">{{ badge }}</span>
          <span v-for="n in result.notes" :key="n">{{ n }}</span>
          <span v-for="a in result.attribution" :key="a.text">Source: <a :href="a.url" target="_blank" rel="noopener noreferrer">{{ a.text }}</a></span>
        </div>

        <div class="loading" v-if="loading"><span class="dots"><span></span><span></span><span></span></span> Searching... {{ waited }} s</div>

        <div class="cards" v-if="!loading && jobs.length">
          <job-card v-for="j in jobs" :key="j.id" :job="j" :saved="saved.has(j.id)" with-details with-letter
                    @save="$emit('save', j)" @letter="openLetter"></job-card>
        </div>
        <div class="nothing" v-else-if="!loading && result">
          <b>No vacancies found.</b> Try fewer words, another city, or search in Dutch or French (for example "serveur" or "ober").
        </div>
      </div>

      <!-- cover letter dialog -->
      <div class="modal-back" v-if="letter" @click.self="letter = null">
        <div class="modal" role="dialog" aria-label="Cover letter">
          <h3>Cover letter</h3>
          <p class="meta">{{ letter.job.title }} - {{ letter.job.company }}</p>
          <div class="row" v-if="!letter.loading">
            <label>Language
              <select v-model="letter.language"><option value="">Same as the ad</option><option value="nl">Dutch</option><option value="fr">French</option><option value="en">English</option></select>
            </label>
            <label>Tone
              <select v-model="letter.tone"><option value="friendly">Friendly</option><option value="professional">Professional</option><option value="enthusiastic">Enthusiastic</option></select>
            </label>
            <button class="btn primary" @click="writeLetter">{{ letter.text ? 'Write again' : 'Write the letter' }}</button>
          </div>
          <div class="loading" v-if="letter.loading"><span class="dots"><span></span><span></span><span></span></span> Writing... {{ Math.round((now - letter.started) / 1000) }} s (a local model can take a minute)</div>
          <div class="msg-error" v-if="letter.error">{{ letter.error }}</div>
          <pre class="letter-text" v-if="letter.text">{{ letter.text }}</pre>
          <p class="meta" v-if="letter.text">{{ letter.words }} words. Check the facts before you send it.</p>
          <div class="row end">
            <button class="btn" v-if="letter.text" @click="copyLetter">Copy</button>
            <button class="btn" @click="letter = null">Close</button>
          </div>
        </div>
      </div>
    </section>`,
};
