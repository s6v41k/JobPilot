/* Shared pieces used by several tabs: the API helper and the vacancy card.
 * (Each tab lives in its own file: tab-jobs.js, tab-tracker.js, tab-profile.js.)
 */
window.JPUI = window.JPUI || {};

// fetch() that returns the JSON body or throws an Error with the server's friendly message.
JPUI.api = async function api(path, options) {
  let res;
  try {
    res = await fetch(path, options);
  } catch (e) {
    throw new Error("Cannot reach the JobPilot server. Is it still running?");
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || "The server answered " + res.status);
  return body;
};

JPUI.jsonBody = (data) => ({ headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

// One vacancy. Props: job, saved. Optional buttons: details, letter. Emits: save, letter.
JPUI.JobCard = {
  props: {
    job: { type: Object, required: true },
    saved: { type: Boolean, default: false },
    withDetails: { type: Boolean, default: false },
    withLetter: { type: Boolean, default: false },
  },
  emits: ["save", "letter"],
  data() { return { open: false }; },
  computed: {
    meta() { return [this.job.company, this.job.city, this.job.region].filter(Boolean).join(" · "); },
    date() { return this.job.posted_at ? this.job.posted_at.slice(0, 10) : ""; },
  },
  template: `
    <article class="job">
      <div class="top-row">
        <h4>{{ job.title }}</h4>
        <span class="score" :class="job.match.label" v-if="job.match" :title="'Match score: ' + job.match.label">{{ job.match.score }}</span>
      </div>
      <div class="meta">{{ meta }}</div>
      <div class="tags">
        <span class="tag type" v-if="job.job_type">{{ job.job_type }}</span>
        <span class="tag" v-if="job.language">{{ job.language.toUpperCase() }}</span>
        <span class="tag" v-if="date">{{ date }}</span>
      </div>
      <p class="reason" v-if="job.match">{{ job.match.reason }}</p>
      <div class="skills" v-if="job.match">
        <span class="skill have" v-for="s in job.match.matched_skills" :key="'h' + s">{{ s }}</span>
        <span class="skill lack" v-for="s in job.match.missing_skills" :key="'l' + s">{{ s }}</span>
      </div>
      <p class="desc" v-if="open && job.description">{{ job.description }}</p>
      <div class="actions">
        <a class="btn" v-if="job.url" :href="job.url" target="_blank" rel="noopener noreferrer">Open ad</a>
        <button class="btn" v-if="withDetails && job.description" @click="open = !open">{{ open ? 'Hide' : 'Details' }}</button>
        <button class="btn" v-if="withLetter" @click="$emit('letter', job)">Letter</button>
        <button class="btn primary" :disabled="saved" @click="$emit('save', job)">{{ saved ? 'Saved' : 'Save' }}</button>
      </div>
    </article>`,
};
