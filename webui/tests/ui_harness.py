"""Frontend harness for webui/index.html.  **macOS only.**

The page ships one inline <script>. There is no build step and no Node
dependency, so rather than add one, this pulls that script out of the HTML and
runs *the real code* against a hand-written DOM stub under JavaScriptCore, via
``osascript -l JavaScript``. That binary is macOS-only, which is why this is a
standalone script and not a unittest module -- ``unittest discover`` must not
pick it up and fail the suite on Linux or CI.

It boots the page four times: fresh, reopened with saved settings, seeded with
stale settings from an older build, and with a job that finished with failures.

Run from the repository root::

    uv run python webui/tests/ui_harness.py

Exits non-zero if any assertion fails. Because the DOM is stubbed, this checks
behaviour (state, wiring, persistence), never rendering.
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HTML = Path(__file__).resolve().parent.parent / "index.html"
script = re.search(r"<script>(.*)</script>", HTML.read_text(), re.S).group(1)

PRELUDE = """
const out = [];
function log(...a) { out.push(a.join(" ")); }
function check(name, cond, extra) {
  log((cond ? "PASS" : "FAIL") + " :: " + name + (extra ? "  [" + extra + "]" : ""));
}

class El {
  constructor(id) {
    this.id = id; this._html = ""; this._text = ""; this.value = "";
    this.disabled = false; this.checked = false; this.style = {};
    this.children = []; this._classes = new Set(); this._listeners = {};
    this.classList = {
      add: (c) => this._classes.add(c),
      remove: (c) => this._classes.delete(c),
      contains: (c) => this._classes.has(c),
    };
  }
  addEventListener(ev, fn) { (this._listeners[ev] = this._listeners[ev] || []).push(fn); }
  scrollIntoView() {}
  set textContent(v) { this._text = String(v); }   // the real setter coerces
  get textContent() { return this._text; }
  fire(ev) {
    (this._listeners[ev] || []).forEach((fn) => fn());
    if (ev === "change" && this.onchange) this.onchange();
  }
  set innerHTML(v) {
    this._html = v;
    const m = /id="([^"]+)"/.exec(v);   // re-register elements recreated via innerHTML
    if (m) DOM[m[1]] = new El(m[1]);
    if (v === "") this.children = [];
  }
  get innerHTML() { return this._html; }
  // a <select> shows its first <option> until told otherwise
  appendChild(c) { this.children.push(c); if (!this.value && c.value) this.value = c.value; }
}

const DOM = {};
for (const id of ["fieldChips","namePreviewWrap","namePreview","date_format","startBtn",
                  "links","image_format","video_preference","folder_mode",
                  "write_mtime","include_metadata","image_download",
                  "video_download","live_download","cookie","proxy","progressCard",
                  "resultBanner","log","barFill","progressTitle","downloadDir",
                  "stProgress","stSuccess","stSkipped","stFailed","resetBtn","overwrite",
                  "failedBox","failedCount","failedList","retryBtn"]) DOM[id] = new El(id);

// mirror the defaults the markup ships with
DOM.image_format.value = "JPEG";
DOM.video_preference.value = "resolution";
DOM.image_download.checked = true;
DOM.video_download.checked = true;

const _store = __SEED__;
const KEY = "xhs-webui-settings-v2";
const localStorage = {
  getItem: (k) => (k in _store ? _store[k] : null),
  setItem: (k, v) => { _store[k] = String(v); },
  removeItem: (k) => { delete _store[k]; },
};
const document = { getElementById: (id) => DOM[id], createElement: (t) => new El("__" + t) };
const location = { reload: () => { _store.__reloaded = "1"; } };
const window = {}; const alert = () => {}; const fetch = () => {};
const setInterval = () => {}; const clearInterval = () => {};
"""

RUN1 = """
const chips = () => DOM.fieldChips.children;
const clickChip = (label) => chips().find((c) => c.innerHTML.startsWith(label)).onclick();
const preview = () => DOM.namePreviewWrap._classes.has("warn")
  ? DOM.namePreviewWrap.textContent : DOM.namePreview.textContent;

check("default preview uses _ separator and dotted time",
  preview() === "2024-01-31_18.30.45_Alice_Autumn-in-Kyoto.jpg", preview());

// Deselect all three defaults -- the old code snapped back to defaults here.
clickChip("Publish time"); clickChip("Author name"); clickChip("Title");
check("all fields can be deselected", selected.length === 0, JSON.stringify(selected));
check("empty selection warns", DOM.namePreviewWrap._classes.has("warn"));
check("empty selection disables start", DOM.startBtn.disabled === true);

// Title can now be made the FIRST field -- the reported bug.
clickChip("Title"); clickChip("Publish time");
check("title can be selected first", JSON.stringify(selected) === '["title","publish_time"]', JSON.stringify(selected));
check("start re-enabled", DOM.startBtn.disabled === false);
check("preview reflects new order", preview() === "Autumn-in-Kyoto_2024-01-31_18.30.45.jpg", preview());

DOM.date_format.value = "date_compact";
DOM.date_format.fire("change");
check("date format applies to preview", preview() === "Autumn-in-Kyoto_20240131.jpg", preview());
check("date format is sent to the API", collectOptions().date_format === "date_compact");
check("date select populated", DOM.date_format.children.length === 8);

// Persistence: editing any watched input writes through to localStorage.
DOM.cookie.value = "a1=xyz; web_session=secret"; DOM.cookie.fire("input");
DOM.live_download.checked = true; DOM.live_download.fire("change");
DOM.proxy.value = "http://127.0.0.1:7890"; DOM.proxy.fire("input");

const saved = JSON.parse(localStorage.getItem(KEY) || "null");
check("settings were persisted", saved !== null);
check("chip order persisted", JSON.stringify(saved.name_fields) === '["title","publish_time"]');
check("date format persisted", saved.date_format === "date_compact");
check("toggle persisted", saved.live_download === true);
check("cookie persisted verbatim", saved.cookie === "a1=xyz; web_session=secret");
check("links are NOT persisted", !("links" in saved), Object.keys(saved).join(","));

const snapshot = localStorage.getItem(KEY);

// Reset clears storage and reloads.
resetSettings();
check("reset clears storage", localStorage.getItem(KEY) === null);
check("reset reloads the page", _store.__reloaded === "1");

return JSON.stringify({ log: out, saved: snapshot });
"""

RUN2 = """
const preview = () => DOM.namePreview.textContent;
check("chip order restored", JSON.stringify(selected) === '["title","publish_time"]', JSON.stringify(selected));
check("date format restored", DOM.date_format.value === "date_compact", DOM.date_format.value);
check("text input restored", DOM.proxy.value === "http://127.0.0.1:7890", DOM.proxy.value);
check("cookie restored", DOM.cookie.value === "a1=xyz; web_session=secret");
check("toggle restored", DOM.live_download.checked === true);
check("unsaved toggle keeps markup default", DOM.image_download.checked === true);
check("preview rebuilt from restored state", preview() === "Autumn-in-Kyoto_20240131.jpg", preview());
check("restored state re-enables start", DOM.startBtn.disabled === false);
return JSON.stringify({ log: out });
"""

RUN3 = """
check("stale select value ignored", DOM.date_format.value === "datetime", DOM.date_format.value);
check("unknown name field dropped", JSON.stringify(selected) === '["title"]', JSON.stringify(selected));
check("corrupt entry does not break boot", DOM.image_format.value === "JPEG", DOM.image_format.value);
return JSON.stringify({ log: out });
"""

RUN4 = """
const hidden = (id) => DOM[id]._classes.has("hidden");
const job = (over) => Object.assign(
  { failed: 0, skipped: 0, success: 3, file_count: 5, size_bytes: 2048,
    output_dir: "/repo/Downloads", failed_links: [] }, over);

// A clean run: no retry affordance, and the destination is stated.
done(job({}));
check("clean run hides the retry box", hidden("failedBox"));
check("clean run names the destination", DOM.resultBanner.innerHTML.indexOf("/repo/Downloads") !== -1);
check("clean run counts saved files", DOM.resultBanner.innerHTML.indexOf("<b>5</b> file(s)") !== -1);
check("clean run title", DOM.progressTitle.textContent === "✅ Done");

// Skips are reported rather than silently dropped.
done(job({ skipped: 4, success: 1 }));
check("skips are surfaced", DOM.resultBanner.innerHTML.indexOf("4 link(s) already downloaded") !== -1, DOM.resultBanner.innerHTML);

// A partial run: failures listed, retry offered.
const bad = ["https://www.xiaohongshu.com/explore/aaa", "https://xhslink.com/bbb"];
done(job({ failed: 2, success: 3, failed_links: bad }));
check("failures reveal the retry box", !hidden("failedBox"));
check("failed count shown", DOM.failedCount.textContent === "2");
check("failed links are listed", DOM.failedList.innerHTML.indexOf("xhslink.com/bbb") !== -1);
check("retry label pluralises", DOM.retryBtn.textContent === "↻ Retry 2 failed links", DOM.retryBtn.textContent);
check("title flags the failures", DOM.progressTitle.textContent === "⚠️ Finished with failures");

// Singular wording for a lone failure.
done(job({ failed: 1, success: 1, failed_links: ["https://xhslink.com/bbb"] }));
check("retry label singular", DOM.retryBtn.textContent === "↻ Retry 1 failed link", DOM.retryBtn.textContent);

// Retry re-runs only the failed links.
DOM.links.value = "the original batch of ten links";
DOM.retryBtn.onclick();
check("retry loads only the failed links", DOM.links.value === "https://xhslink.com/bbb", DOM.links.value);
check("retry starts a job", running === true);
check("retry is disabled while running", DOM.retryBtn.disabled === true);
check("start is disabled while running", DOM.startBtn.disabled === true);

// A total failure (status=error) still offers the retry.
finish(false, "Nothing was downloaded.");
renderFailed({ failed_links: bad });
check("total failure still offers retry", !hidden("failedBox"));

// The overwrite toggle reaches the API.
DOM.overwrite.checked = true;
check("overwrite is sent to the API", collectOptions().overwrite === true);
check("removed options are not sent", !("folder_name" in collectOptions()) && !("author_archive" in collectOptions()));
return JSON.stringify({ log: out });
"""


def run(tests: str, seed: dict) -> dict:
    # JXA reserves a global `$`, so run the page script inside a function scope.
    js = "(function(){\n" + PRELUDE.replace("__SEED__", json.dumps(seed)) + script + tests + "\n})()"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(js)
    r = subprocess.run(["osascript", "-l", "JavaScript", fh.name], capture_output=True, text=True)
    Path(fh.name).unlink(missing_ok=True)
    if r.returncode != 0:
        raise SystemExit(f"JS error:\n{r.stderr}")
    return json.loads(r.stdout)


KEY = "xhs-webui-settings-v2"

results: list[str] = []


def boot(label: str, tests: str, seed: dict) -> dict:
    print(f"--- {label}")
    outcome = run(tests, seed)
    print("\n".join(outcome["log"]))
    results.extend(outcome["log"])
    print()
    return outcome


KEY = "xhs-webui-settings-v2"

first = boot("boot 1: fresh browser, drive the UI", RUN1, {})
boot("boot 2: reopened with the saved settings", RUN2, {KEY: first["saved"]})
stale = json.dumps({"name_fields": ["title", "no_such_field"], "date_format": "%Y/%m/%d",
                    "proxy": 42, "image_format": "GIF"})
boot("boot 3: storage holds stale/corrupt values from an older build", RUN3, {KEY: stale})
boot("boot 4: failed links, retry and skip reporting", RUN4, {})

failures = [line for line in results if line.startswith("FAIL")]
print(f"{len(results) - len(failures)} passed, {len(failures)} failed")
sys.exit(1 if failures else 0)
