// dubhash landing page (templates/landing.html): the two phones play a short interpreted
// call per use case, and sections fade in as they scroll into view. No dependencies.
// Marathi, Gujarati and Kannada lines need a native speaker's check (PQ5).
(function () {
  "use strict";

  const SCENES = {
    delivery: {
      caption: "A Marathi-speaking customer and a Hindi-speaking delivery rider, on one ordinary call.",
      a: { name: "Sunita", initial: "S", lang: "मराठी" },
      b: { name: "Ravi · Delivery", initial: "R", lang: "हिन्दी" },
      lines: [
        { from: "a", src: "मी घरी नाही, पार्सल वॉचमनकडे द्या.", tgt: "मैं घर पर नहीं हूँ, पार्सल वॉचमैन को दे दीजिए।", ms: 1310 },
        { from: "b", src: "ठीक है, ओटीपी बता दीजिए।", tgt: "ठीक आहे, ओटीपी सांगा.", ms: 1180 },
        { from: "a", src: "ओटीपी 47296 आहे.", tgt: "ओटीपी 47296 है।", ms: 1390, check: "47296" },
      ],
    },
    clinic: {
      caption: "A Gujarati-speaking patient and an English-speaking doctor on a teleconsult.",
      a: { name: "Hansaben", initial: "H", lang: "ગુજરાતી" },
      b: { name: "Dr. Mehta", initial: "M", lang: "English" },
      lines: [
        { from: "a", src: "મને ત્રણ દિવસથી તાવ છે.", tgt: "I've had a fever for three days.", ms: 1270 },
        { from: "b", src: "Take one tablet twice a day, after food.", tgt: "દિવસમાં બે વાર, જમ્યા પછી એક ગોળી લો.", ms: 1420 },
        { from: "a", src: "બરાબર, આભાર ડૉક્ટર.", tgt: "Okay, thank you, doctor.", ms: 1090 },
      ],
    },
    rent: {
      caption: "A Kannada-speaking tenant and a Hindi-speaking landlord sorting out a repair.",
      a: { name: "Kiran", initial: "K", lang: "ಕನ್ನಡ" },
      b: { name: "Sharma ji", initial: "S", lang: "हिन्दी" },
      lines: [
        { from: "a", src: "ಎರಡು ದಿನದಿಂದ ನೀರು ಬರುತ್ತಿಲ್ಲ.", tgt: "दो दिन से पानी नहीं आ रहा है।", ms: 1360 },
        { from: "b", src: "आज शाम 6 बजे प्लंबर भेजता हूँ।", tgt: "ಇಂದು ಸಂಜೆ 6 ಗಂಟೆಗೆ ಪ್ಲಂಬರ್ ಕಳುಹಿಸುತ್ತೇನೆ.", ms: 1330, check: "6" },
        { from: "a", src: "ಸರಿ, ಧನ್ಯವಾದಗಳು.", tgt: "ठीक है, धन्यवाद।", ms: 1040 },
      ],
    },
  };
  const ORDER = ["delivery", "clinic", "rent"];
  const MAX_BUBBLES = 4;

  const stage = document.getElementById("stage");
  const caption = document.getElementById("stage-caption");
  const bridge = stage && stage.querySelector(".bridge");
  const bridgeText = bridge && bridge.querySelector('[data-role="bridge"]');
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function phone(side) {
    const root = stage.querySelector(`.iphone[data-side="${side}"]`);
    const q = (r) => root.querySelector(`[data-role="${r}"]`);
    return { root, convo: q("convo"), status: q("status"), timer: q("timer"),
             name: q("name"), avatar: q("avatar"), lang: q("lang") };
  }

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function bubble(p, kind, label, text) {
    const b = el("div", `bubble ${kind}`);
    b.append(el("small", null, label));
    const body = el("span", null, text);
    b.append(body);
    p.convo.append(b);
    while (p.convo.children.length > MAX_BUBBLES) p.convo.firstElementChild.remove();
    return { b, body };
  }

  if (stage) {
    const P = { a: phone("a"), b: phone("b") };
    let run = 0; // bumped on every scene change: stale loops stop at their next await
    let auto = true;
    let t0 = performance.now();

    const sleep = (ms, id) =>
      new Promise((res, rej) => setTimeout(() => (id === run ? res() : rej("stale")), ms));

    function setState(side, state, text) {
      const p = P[side];
      p.root.classList.toggle("speaking", state === "speaking");
      p.root.classList.toggle("hearing", state === "hearing");
      p.root.classList.toggle("active", state !== "idle");
      p.status.textContent = text;
    }

    function setScene(key) {
      const s = SCENES[key];
      for (const side of ["a", "b"]) {
        const p = P[side];
        const who = s[side];
        p.name.textContent = who.name;
        p.avatar.textContent = who.initial;
        p.lang.textContent = who.lang;
        p.convo.replaceChildren();
        setState(side, "idle", "Listening");
      }
      caption.textContent = s.caption;
      bridge.dataset.dir = "";
      bridgeText.textContent = "listening";
      for (const tab of stage.querySelectorAll(".scene-tab"))
        tab.setAttribute("aria-selected", String(tab.dataset.scene === key));
      t0 = performance.now();
    }

    async function speak(s, line, id) {
      const from = line.from;
      const to = from === "a" ? "b" : "a";
      const words = line.src.split(" ");
      setState(from, "speaking", "You're speaking…");
      setState(to, "idle", `${s[from].name.split(" ")[0]} is speaking…`);
      const mine = bubble(P[from], "mine", "You", "");
      mine.body.classList.add("typing");
      for (let i = 0; i < words.length; i++) {
        mine.body.textContent = words.slice(0, i + 1).join(" ");
        await sleep(230, id);
      }
      mine.body.classList.remove("typing");
      // the pause, then dubhash interprets
      setState(from, "idle", "Interpreting…");
      bridge.dataset.dir = from + to;
      bridgeText.textContent = `${s[from].lang} → ${s[to].lang}`;
      await sleep(Math.min(line.ms, 1200), id);
      setState(to, "hearing", `Hearing ${s[from].name.split(" ")[0]}, in ${s[to].lang}`);
      const heard = bubble(P[to], "heard", `${s[from].name.split(" ")[0]} · translated`, line.tgt);
      const meta = el("div", "meta");
      meta.append(el("span", "tag time", `${(line.ms / 1000).toFixed(1)} s`));
      if (line.check) meta.append(el("span", "tag", `${line.check} ✓ checked`));
      heard.b.append(meta);
      await sleep(1700 + line.tgt.length * 18, id);
      bridge.dataset.dir = "";
      bridgeText.textContent = "listening";
      setState(to, "idle", "Listening");
      setState(from, "idle", "Listening");
      await sleep(450, id);
    }

    async function play(key) {
      const id = ++run;
      setScene(key);
      const s = SCENES[key];
      try {
        await sleep(700, id);
        for (const line of s.lines) await speak(s, line, id);
        await sleep(2600, id);
        if (auto) play(ORDER[(ORDER.indexOf(key) + 1) % ORDER.length]);
        else play(key);
      } catch (e) {
        if (e !== "stale") throw e;
      }
    }

    function staticScene(key) {
      run++;
      setScene(key);
      const s = SCENES[key];
      for (const line of s.lines) {
        const to = line.from === "a" ? "b" : "a";
        bubble(P[line.from], "mine", "You", line.src);
        bubble(P[to], "heard", `${s[line.from].name.split(" ")[0]} · translated`, line.tgt);
      }
    }

    setInterval(() => {
      const s = Math.floor((performance.now() - t0) / 1000);
      const txt = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
      P.a.timer.textContent = txt;
      P.b.timer.textContent = txt;
    }, 500);

    stage.querySelectorAll(".scene-tab").forEach((tab) =>
      tab.addEventListener("click", () => {
        auto = false;
        if (reduced) staticScene(tab.dataset.scene);
        else play(tab.dataset.scene);
      })
    );

    if (reduced) staticScene("delivery");
    else play("delivery");
  }

  // sections fade in on scroll; the timing races start when they are seen
  const targets = document.querySelectorAll(".section > *, .langs, .timing-card");
  if ("IntersectionObserver" in window && !reduced) {
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          e.target.classList.add("in-view");
          io.unobserve(e.target);
        }
      },
      { threshold: 0.15 }
    );
    targets.forEach((t) => {
      t.classList.add("reveal");
      io.observe(t);
    });
  }
})();
