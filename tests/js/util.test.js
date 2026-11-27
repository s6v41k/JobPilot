// Tests for web/util.js. Run:  node --test tests/js
const test = require("node:test");
const assert = require("node:assert");
const JP = require("../../web/util.js");

test("html is always escaped (no injection from a model answer)", () => {
  const html = JP.renderMarkdown('<img src=x onerror=alert(1)> <script>alert(2)</script> "quoted"');
  assert.ok(!html.includes("<img") && !html.includes("<script"));
  assert.ok(html.includes("&lt;img") && html.includes("&quot;quoted&quot;"));
});

test("only http(s) links become links; javascript: does not", () => {
  const html = JP.renderMarkdown("[ok](https://example.com/a?b=1&c=2) and [bad](javascript:alert(1))");
  assert.ok(html.includes('<a href="https://example.com/a?b=1&amp;c=2" target="_blank" rel="noopener noreferrer">ok</a>'));
  assert.ok(!html.includes("href=\"javascript"));
});

test("bare links are linked once and trailing punctuation stays outside", () => {
  const html = JP.renderMarkdown("See https://www.leforem.be/offre/123.");
  assert.strictEqual((html.match(/<a /g) || []).length, 1);
  assert.ok(html.includes('>https://www.leforem.be/offre/123</a>.'));
});

test("a link whose text is a url is not wrapped twice", () => {
  const html = JP.renderMarkdown("[https://a.be/x](https://a.be/x)");
  assert.strictEqual((html.match(/<a /g) || []).length, 1);
});

test("numbered and bulleted lists, bold, italic, code", () => {
  const html = JP.renderMarkdown("Intro\n\n1. **Serveur** - Accent (id: forem:1)\n2. Cook\n\n- one\n- two\n\nUse `x` and *this*.");
  assert.ok(html.includes("<ol><li><strong>Serveur</strong> - Accent (id: forem:1)</li><li>Cook</li></ol>"));
  assert.ok(html.includes("<ul><li>one</li><li>two</li></ul>"));
  assert.ok(html.includes("<code>x</code>") && html.includes("<em>this</em>"));
});

test("headings become bold lines; line breaks inside a paragraph are kept", () => {
  assert.ok(JP.renderMarkdown("### Title").includes("<p><strong>Title</strong></p>"));
  assert.ok(JP.renderMarkdown("a\nb").includes("a<br>b"));
});

test("empty and null input", () => {
  assert.strictEqual(JP.renderMarkdown(""), "");
  assert.strictEqual(JP.renderMarkdown(null), "");
});

test("SSE parser handles events split across chunks and CRLF", () => {
  const seen = [];
  const feed = JP.createSSEParser((e, d) => seen.push([e, d]));
  feed('event: token\ndata: {"text": "Hel');
  feed('lo"}\n\nevent: done\r\ndata: {"ok": tr');
  feed("ue}\r\n\r\n");
  assert.deepStrictEqual(seen, [["token", { text: "Hello" }], ["done", { ok: true }]]);
});

test("SSE parser skips broken json and comment-only blocks", () => {
  const seen = [];
  const feed = JP.createSSEParser((e, d) => seen.push(e));
  feed("event: token\ndata: {broken\n\n: keepalive\n\nevent: done\ndata: {}\n\n");
  assert.deepStrictEqual(seen, ["done"]);
});

test("formatters", () => {
  assert.strictEqual(JP.formatMs(420), "420 ms");
  assert.strictEqual(JP.formatMs(2300), "2.3 s");
  assert.strictEqual(JP.formatMs(65000), "1 min 5 s");
  assert.strictEqual(JP.summarizeArgs({ city: "Gent", limit: 5 }), 'city="Gent", limit=5');
  assert.ok(JP.summarizeArgs({ x: "a".repeat(300) }).length <= 90);
  assert.strictEqual(JP.ageLabel(0.2), "updated just now");
  assert.strictEqual(JP.ageLabel(5.4), "updated 5 h ago");
  assert.strictEqual(JP.ageLabel(72), "updated 3 days ago");
  assert.strictEqual(JP.sourceLabel({ kind: "snapshot", date: "2026-11-27" }), "Saved snapshot from 2026-11-27");
  assert.strictEqual(JP.sourceLabel({ kind: "live" }), "Live data");
});

test("tool result summaries", () => {
  assert.strictEqual(JP.summarizeResult("search_jobs", true, { count: 5, jobs: [], data_source: "live" }, null), "5 vacancies (live)");
  assert.strictEqual(JP.summarizeResult("match_cv", true, { matches: [1, 2] }, null), "2 vacancies scored");
  assert.strictEqual(JP.summarizeResult("x", false, null, "boom"), "boom");
  assert.strictEqual(JP.summarizeResult("draft_cover_letter", true, { letter: "x", word_count: 120, language: "nl" }, null), "letter of 120 words (nl)");
});
