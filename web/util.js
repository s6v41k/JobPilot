/* Small pure helpers for the web UI (no Vue, no DOM), so they can be tested with Node.
 *
 *  - renderMarkdown: safe mini-Markdown -> HTML. ALL text is escaped first, then a few
 *    patterns (bold, lists, links) are turned into tags, so a model can never inject HTML.
 *  - createSSEParser: turns the text stream of /api/chat into (event, data) calls.
 *  - small formatters used by the cards and the trace.
 */
(function (root) {
  "use strict";

  function escapeHtml(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // Inline formatting on text that is ALREADY escaped.
  function inline(s) {
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[\s(])\*([^*\s][^*]*?)\*(?=[\s).,!?:;]|$)/g, "$1<em>$2</em>");
    // [text](https://...) - only http(s) links are allowed
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    // bare links (not the ones we just made: those follow a quote or '>')
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?])/g,
      '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
    return s;
  }

  function renderMarkdown(text) {
    const lines = escapeHtml(text).replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let list = null;     // "ul" | "ol" | null
    let para = [];
    const closeList = () => { if (list) { out.push("</" + list + ">"); list = null; } };
    const flush = () => { if (para.length) { out.push("<p>" + inline(para.join("<br>")) + "</p>"); para = []; } };
    for (const line of lines) {
      const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
      const ul = line.match(/^\s*[-*•]\s+(.*)$/);
      const heading = line.match(/^\s*#{1,6}\s+(.*)$/);
      if (ol || ul) {
        flush();
        const kind = ol ? "ol" : "ul";
        if (list !== kind) { closeList(); out.push("<" + kind + ">"); list = kind; }
        out.push("<li>" + inline((ol || ul)[1]) + "</li>");
      } else if (heading) {
        flush(); closeList();
        out.push("<p><strong>" + inline(heading[1]) + "</strong></p>");
      } else if (!line.trim()) {
        flush(); closeList();
      } else {
        closeList();
        para.push(line);
      }
    }
    flush(); closeList();
    return out.join("");
  }

  // Server-Sent Events: "event: x\ndata: {json}\n\n". Chunks may cut an event in half.
  function createSSEParser(onEvent) {
    let buffer = "";
    return function feed(chunk) {
      buffer = (buffer + chunk).replace(/\r\n/g, "\n");
      let end;
      while ((end = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        let event = "message";
        const data = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
        }
        if (!data.length) continue;
        let payload;
        try { payload = JSON.parse(data.join("\n")); } catch (e) { continue; }   // skip a broken event
        onEvent(event, payload);
      }
    };
  }

  function formatMs(ms) {
    if (ms == null) return "";
    if (ms < 1000) return Math.round(ms) + " ms";
    const s = ms / 1000;
    if (s < 60) return s.toFixed(1) + " s";
    return Math.floor(s / 60) + " min " + Math.round(s % 60) + " s";
  }

  function summarizeArgs(args, max) {
    max = max || 90;
    const text = Object.entries(args || {}).map(([k, v]) => k + "=" + JSON.stringify(v)).join(", ");
    return text.length > max ? text.slice(0, max - 1) + "…" : text;
  }

  // One line describing what a tool returned (shown in the trace).
  function summarizeResult(name, ok, data, error) {
    if (!ok) return error || "failed";
    if (!data || typeof data !== "object") return "ok";
    if (Array.isArray(data.matches)) return data.matches.length + " vacancies scored";
    if (typeof data.count === "number" && Array.isArray(data.jobs)) {
      return data.count + " vacancies (" + (data.data_source || "live") + ")";
    }
    if (data.letter) return "letter of " + data.word_count + " words (" + data.language + ")";
    if (data.profile) return "profile saved";
    if (data.saved || data.updated) return "tracker updated";
    if (Array.isArray(data.applications)) return data.count + " applications";
    if (data.title) return data.title;
    return "ok";
  }

  function sourceLabel(source) {
    if (!source) return "";
    if (source.kind === "live") return "Live data";
    if (source.kind === "cached") return "Cached real data";
    if (source.kind === "snapshot") return "Saved snapshot" + (source.date ? " from " + source.date : "");
    return source.kind;
  }

  function ageLabel(hours) {
    if (hours == null) return "";
    if (hours < 1) return "updated just now";
    if (hours < 48) return "updated " + Math.round(hours) + " h ago";
    return "updated " + Math.round(hours / 24) + " days ago";
  }

  // Profile lists are edited as one item per line (or separated by commas/semicolons).
  function splitList(text) {
    return String(text == null ? "" : text).split(/[\n;]+/)
      .flatMap((line) => (line.includes(",") && line.split(",").every((p) => p.trim().split(/\s+/).length <= 4)
        ? line.split(",") : [line]))
      .map((s) => s.trim().replace(/^[-*•]\s*/, "")).filter(Boolean);
  }
  function joinList(items) { return (items || []).join("\n"); }

  // "2026-11-27T10:00:00+00:00" -> "27 Nov 2026"
  function shortDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return isNaN(d) ? String(iso).slice(0, 10)
      : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
  }

  const STATUSES = [
    { id: "interested", label: "Interested" }, { id: "applied", label: "Applied" },
    { id: "interview", label: "Interview" }, { id: "offer", label: "Offer" }, { id: "rejected", label: "Rejected" },
  ];

  const api = { escapeHtml, renderMarkdown, createSSEParser, formatMs, summarizeArgs,
                summarizeResult, sourceLabel, ageLabel, splitList, joinList, shortDate, STATUSES };
  root.JP = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
