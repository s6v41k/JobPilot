/* Tracker tab: a kanban board of the vacancies the student follows. Works without the LLM. */
JPUI.TrackerTab = {
  props: { active: Boolean },
  emits: ["notify", "changed"],
  data() { return { items: [], loading: false, loaded: false, columns: JP.STATUSES }; },
  watch: { active(now) { if (now) this.load(); } },     // reload every time the tab is opened
  mounted() { if (this.active) this.load(); },
  methods: {
    inColumn(status) { return this.items.filter((a) => a.status === status); },
    date: JP.shortDate,
    async load() {
      this.loading = true;
      try {
        this.items = (await JPUI.api("/api/applications")).applications;
        this.loaded = true;
      } catch (e) {
        this.$emit("notify", "error", "Could not load the tracker: " + e.message);
      } finally {
        this.loading = false;
      }
    },
    async change(item, body) {
      try {
        await JPUI.api("/api/applications/" + encodeURIComponent(item.job_id),
          { method: "PATCH", ...JPUI.jsonBody(body) });
        await this.load();
      } catch (e) {
        this.$emit("notify", "error", "Could not update: " + e.message);
        await this.load();
      }
    },
    move(item, status) { return this.change(item, { status }); },
    note(item, text) { return this.change(item, { notes: text }); },
    async remove(item) {
      try {
        await JPUI.api("/api/applications/" + encodeURIComponent(item.job_id), { method: "DELETE" });
        this.$emit("changed");
        await this.load();
      } catch (e) {
        this.$emit("notify", "error", "Could not remove: " + e.message);
      }
    },
  },
  template: `
    <section class="page tabpage">
      <div class="col wide">
        <p class="hint" v-if="loaded && !items.length">
          Nothing tracked yet. Save vacancies from the Chat or Jobs tab and follow them here:
          interested, applied, interview, offer or rejected.
        </p>
        <div class="board">
          <div class="column" v-for="c in columns" :key="c.id" :data-status="c.id">
            <h3>{{ c.label }} <span class="count">{{ inColumn(c.id).length }}</span></h3>
            <div class="empty-col" v-if="!inColumn(c.id).length">Nothing here yet</div>
            <article class="job small" v-for="a in inColumn(c.id)" :key="a.job_id">
              <h4>{{ a.title || a.job_id }}</h4>
              <div class="meta">{{ [a.company, a.city].filter(Boolean).join(' · ') }}</div>
              <select :value="a.status" @change="move(a, $event.target.value)" aria-label="Status">
                <option v-for="s in columns" :key="s.id" :value="s.id">{{ s.label }}</option>
              </select>
              <input class="note" :value="a.notes || ''" maxlength="500" placeholder="Add a note..." @change="note(a, $event.target.value)" aria-label="Note">
              <div class="meta">Updated {{ date(a.updated_at) }}</div>
              <div class="actions">
                <a class="btn" v-if="a.url" :href="a.url" target="_blank" rel="noopener noreferrer">Open ad</a>
                <button class="btn" @click="remove(a)" title="Remove from the tracker">Remove</button>
              </div>
            </article>
          </div>
        </div>
      </div>
    </section>`,
};
