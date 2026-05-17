// Mirsad — minimal vanilla JS for rendering the public feed.
// Reads ../data/posts.json (relative to /site) and renders cards.

const MECH_LABELS = {
  default: "Default",
  social_proof: "Social proof",
  loss_framing: "Loss framing",
  gain_framing: "Gain framing",
  salience: "Salience",
  friction_added: "Friction added",
  friction_removed: "Friction removed",
  commitment_device: "Commitment device",
  implementation_intention: "Implementation intention",
  feedback_loop: "Feedback loop",
  reminder: "Reminder",
  incentive: "Incentive",
  disclosure: "Disclosure",
  choice_architecture: "Choice architecture",
  identity_priming: "Identity priming",
  personalisation: "Personalisation",
  gamification: "Gamification",
  deceptive_pattern: "Deceptive pattern",
  sludge: "Sludge",
};

const state = { posts: [], beat: "all", query: "" };

async function loadPosts() {
  // Try a few candidate paths — depending on whether the site is served
  // from /site/ or from the repo root, the relative path to data differs.
const candidates = ["posts.json", "../data/posts.json", "data/posts.json", "./data/posts.json"];  for (const path of candidates) {
    try {
      const r = await fetch(path, { cache: "no-store" });
      if (!r.ok) continue;
      return await r.json();
    } catch (e) { /* try next */ }
  }
  return [];
}

function fmtDate(iso) {
  if (!iso) return "";
  // Display as e.g. "17 May 2026"
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function applyFilters(posts) {
  let out = posts;
  if (state.beat !== "all") {
    out = out.filter(p => p.beat === state.beat);
  }
  if (state.query) {
    const q = state.query.toLowerCase();
    out = out.filter(p =>
      (p.title_en || "").toLowerCase().includes(q) ||
      (p.summary_en || "").toLowerCase().includes(q) ||
      (p.source_name || "").toLowerCase().includes(q) ||
      (p.mechanisms || []).some(m => m.toLowerCase().includes(q)) ||
      (p.countries || []).some(c => c.toLowerCase().includes(q))
    );
  }
  return out;
}

function render() {
  const feed = document.getElementById("feed");
  const tpl = document.getElementById("post-template");
  const filtered = applyFilters(state.posts);

  feed.innerHTML = "";
  if (!filtered.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = state.posts.length
      ? "No posts match those filters yet."
      : "The agents haven't posted anything yet. Check back soon.";
    feed.appendChild(empty);
    return;
  }

  for (const p of filtered) {
    const node = tpl.content.cloneNode(true);
    const beat = node.querySelector(".beat");
    beat.classList.add(p.beat);
    beat.textContent = p.beat;

    if (p.flag && p.beat === "product") {
      const f = document.createElement("span");
      f.className = `flag ${p.flag}`;
      f.textContent = p.flag;
      beat.parentNode.appendChild(f);
    }

    const t = node.querySelector("time");
    const date = p.published_at || p.discovered_at;
    t.textContent = fmtDate(date);
    t.setAttribute("datetime", date || "");

    node.querySelector(".title").textContent = p.title_en || "";
    node.querySelector(".title-ar").textContent = p.title_ar || "";
    node.querySelector(".summary").textContent = p.summary_en || "";
    node.querySelector(".verdict").textContent = p.verdict || "";

    const mechs = node.querySelector(".mechanisms");
    for (const m of p.mechanisms || []) {
      const c = document.createElement("span");
      c.className = "mech-chip";
      c.textContent = MECH_LABELS[m] || m;
      mechs.appendChild(c);
    }

    const countries = node.querySelector(".countries");
    for (const ctry of p.countries || []) {
      const c = document.createElement("span");
      c.className = "country-chip";
      c.textContent = ctry;
      countries.appendChild(c);
    }

    const src = node.querySelector(".src");
    src.href = p.source_url || "#";
    src.textContent = (p.source_name || "Source") + " ↗";

    feed.appendChild(node);
  }
}

function wireControls() {
  for (const btn of document.querySelectorAll(".chip")) {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".chip").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      state.beat = btn.dataset.beat;
      render();
    });
  }
  document.getElementById("search").addEventListener("input", (e) => {
    state.query = e.target.value.trim();
    render();
  });
}

function setStamp(posts) {
  if (!posts.length) return;
  const latest = posts.reduce((acc, p) => {
    const d = p.discovered_at || p.published_at || "";
    return d > acc ? d : acc;
  }, "");
  const span = document.getElementById("updated");
  if (latest) span.textContent = "Updated " + fmtDate(latest);
}

(async function init() {
  wireControls();
  state.posts = await loadPosts();
  setStamp(state.posts);
  render();
})();
