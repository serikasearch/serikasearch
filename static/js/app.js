/* SerikaSearch — front-end behaviour.

   No dependencies, no build step, no third-party requests. Everything here is
   progressive enhancement: the site works with JavaScript switched off, and
   this file makes it nicer — autocomplete, a properly justified image grid,
   the lightbox, keyboard navigation, and locally stored preferences.

   Preferences live in localStorage and are never sent to the server. */

(function () {
  "use strict";

  var $  = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  };

  var reduceMotion = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ============================================================ settings == */

  var SETTINGS_KEY = "serika:settings";
  var RECENT_KEY = "serika:recent";

  var defaults = {
    newTab: true,
    suggest: true,
    recent: false,
    answers: true,
    density: "normal"
  };

  function readSettings() {
    try {
      var raw = window.localStorage.getItem(SETTINGS_KEY);
      if (!raw) return Object.assign({}, defaults);
      return Object.assign({}, defaults, JSON.parse(raw));
    } catch (err) {
      return Object.assign({}, defaults);
    }
  }

  function writeSettings(next) {
    try {
      window.localStorage.setItem(SETTINGS_KEY, JSON.stringify(next));
    } catch (err) { /* private mode, quota — the site still works */ }
  }

  var settings = readSettings();

  /* Apply the preferences that affect markup already on the page. */
  function applySettings() {
    if (!settings.newTab) {
      $$(".result h3 a, .image-card, .video-title, .video-thumb").forEach(function (el) {
        el.removeAttribute("target");
      });
    }
    if (!settings.answers) {
      $$(".answer").forEach(function (el) { el.remove(); });
    }
  }

  /* ------------------------------------------------------- recent searches */

  function readRecent() {
    try { return JSON.parse(window.localStorage.getItem(RECENT_KEY) || "[]"); }
    catch (err) { return []; }
  }

  function rememberSearch(query) {
    if (!settings.recent || !query) return;
    var list = readRecent().filter(function (q) { return q !== query; });
    list.unshift(query);
    try {
      window.localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, 8)));
    } catch (err) { /* ignore */ }
  }

  function renderRecent() {
    var row = $("#recentRow");
    if (!row || !settings.recent) return;
    var list = readRecent();
    if (!list.length) return;

    row.hidden = false;
    row.innerHTML = '<span class="recent-label">Recent</span>';
    list.slice(0, 6).forEach(function (query) {
      var a = document.createElement("a");
      a.className = "chip";
      a.href = "/search?q=" + encodeURIComponent(query);
      a.textContent = query;
      row.appendChild(a);
    });
    var clear = document.createElement("button");
    clear.type = "button";
    clear.className = "chip";
    clear.textContent = "Clear";
    clear.addEventListener("click", function () {
      try { window.localStorage.removeItem(RECENT_KEY); } catch (err) {}
      row.hidden = true;
    });
    row.appendChild(clear);
  }

  /* =============================================================== toast == */

  var toastTimer = null;
  function toast(message) {
    var el = $("#toast");
    if (!el) return;
    el.textContent = message;
    el.classList.add("show");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () {
      el.classList.remove("show");
    }, 1800);
  }

  /* ============================================================== copy ==== */

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    /* Fallback for http:// origins, where the async clipboard API is absent. */
    return new Promise(function (resolve, reject) {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy") ? resolve() : reject();
      } catch (err) {
        reject(err);
      } finally {
        document.body.removeChild(area);
      }
    });
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-copy]");
    if (!button) return;
    event.preventDefault();
    var value = button.getAttribute("data-copy");
    copyText(value).then(function () {
      toast("Copied");
      button.classList.add("copied");
      window.setTimeout(function () { button.classList.remove("copied"); }, 1200);
    }).catch(function () {
      toast("Couldn't copy — select it manually");
    });
  });

  /* "Again" on generated answers: re-run the same query for a fresh value. */
  document.addEventListener("click", function (event) {
    if (!event.target.closest("[data-reload]")) return;
    event.preventDefault();
    window.location.reload();
  });

  /* Pronunciation audio on dictionary entries. */
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-audio]");
    if (!button) return;
    event.preventDefault();
    try {
      var audio = new Audio(button.getAttribute("data-audio"));
      audio.play().catch(function () { toast("Audio unavailable"); });
    } catch (err) {
      toast("Audio unavailable");
    }
  });

  /* Images that 404 or are blocked by their origin shouldn't leave a hole.
     Handled by delegation rather than inline onerror, so the page needs no
     inline-script allowance in its Content-Security-Policy. */
  document.addEventListener("error", function (event) {
    var img = event.target;
    if (!img || img.tagName !== "IMG") return;
    var mode = img.getAttribute("data-fallback");
    if (mode === "hide") {
      img.style.display = "none";
    } else if (mode === "hide-parent" && img.parentElement) {
      img.parentElement.style.display = "none";
    }
    var card = img.closest(".image-card");
    if (card) card.classList.add("img-broken");
  }, true);

  /* =========================================================== searchbox == */

  function initSearchbox(box) {
    var input = box.querySelector('input[name="q"]');
    var clear = box.querySelector(".clear-btn");
    if (!input) return;

    function sync() {
      box.classList.toggle("has-value", input.value.length > 0);
    }
    sync();
    input.addEventListener("input", sync);
    input.addEventListener("change", sync);

    if (clear) {
      clear.addEventListener("click", function (event) {
        event.preventDefault();
        input.value = "";
        sync();
        hideSuggestions();
        input.focus();
      });
    }

    if (input.form) {
      input.form.addEventListener("submit", function () {
        rememberSearch(input.value.trim());
      });
    }

    initSuggestions(box, input);
  }

  /* ---------------------------------------------------------- suggestions */

  function initSuggestions(box, input) {
    var panel = box.querySelector(".suggestions");
    if (!panel || !settings.suggest) return;

    var items = [];
    var active = -1;
    var controller = null;
    var debounce = null;
    var lastQuery = "";

    function hide() {
      panel.hidden = true;
      panel.innerHTML = "";
      items = [];
      active = -1;
      input.setAttribute("aria-expanded", "false");
    }
    hideSuggestions = hide;

    function highlight(text, query) {
      var index = text.toLowerCase().indexOf(query.toLowerCase());
      var span = document.createElement("span");
      span.className = "s-text";
      if (index < 0) {
        span.textContent = text;
        return span;
      }
      /* Build with text nodes, never innerHTML — suggestions come from
         crawled page titles and must not be able to inject markup. */
      span.appendChild(document.createTextNode(text.slice(0, index)));
      var strong = document.createElement("b");
      strong.textContent = text.slice(index, index + query.length);
      span.appendChild(strong);
      span.appendChild(document.createTextNode(text.slice(index + query.length)));
      return span;
    }

    function show(list, query) {
      if (!list.length) { hide(); return; }
      panel.innerHTML = "";
      items = list.slice(0, 8);
      active = -1;

      items.forEach(function (text, i) {
        var row = document.createElement("button");
        row.type = "button";
        row.className = "suggestion";
        row.setAttribute("role", "option");
        row.dataset.index = String(i);

        var ico = document.createElement("span");
        ico.className = "s-ico";
        ico.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" ' +
          'fill="none" stroke="currentColor" stroke-width="2.2" ' +
          'stroke-linecap="round"><circle cx="11" cy="11" r="7"/>' +
          '<path d="m20.5 20.5-4.2-4.2"/></svg>';
        row.appendChild(ico);
        row.appendChild(highlight(text, query));

        row.addEventListener("mousedown", function (event) {
          event.preventDefault();
          input.value = text;
          rememberSearch(text);
          if (input.form) input.form.submit();
        });
        panel.appendChild(row);
      });

      panel.hidden = false;
      input.setAttribute("aria-expanded", "true");
    }

    function setActive(next) {
      var rows = $$(".suggestion", panel);
      if (!rows.length) return;
      if (active >= 0 && rows[active]) rows[active].classList.remove("active");
      active = (next + rows.length) % rows.length;
      rows[active].classList.add("active");
      input.value = items[active];
    }

    function fetchSuggestions(query) {
      if (controller) controller.abort();
      controller = ("AbortController" in window) ? new AbortController() : null;
      var options = controller ? { signal: controller.signal } : {};

      fetch("/api/suggest?q=" + encodeURIComponent(query), options)
        .then(function (response) { return response.json(); })
        .then(function (data) {
          if (data && data.query !== lastQuery) return;   // a stale response
          show((data && data.suggestions) || [], query);
        })
        .catch(function () { /* aborted or offline — no suggestions is fine */ });
    }

    input.addEventListener("input", function () {
      var query = input.value.trim();
      lastQuery = query;
      window.clearTimeout(debounce);
      if (query.length < 2) { hide(); return; }
      debounce = window.setTimeout(function () {
        fetchSuggestions(query);
      }, 130);
    });

    input.addEventListener("keydown", function (event) {
      if (panel.hidden) return;
      if (event.key === "ArrowDown") {
        event.preventDefault(); setActive(active + 1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault(); setActive(active - 1);
      } else if (event.key === "Enter") {
        if (active >= 0) rememberSearch(items[active]);
        hide();
      } else if (event.key === "Escape") {
        hide();
      }
    });

    input.addEventListener("blur", function () {
      window.setTimeout(hide, 120);     // let a click on a row land first
    });
  }

  var hideSuggestions = function () {};

  $$(".searchbox").forEach(initSearchbox);
  renderRecent();
  applySettings();

  /* ==================================================== justified images == */

  var ROW_HEIGHTS = { compact: 150, normal: 200, large: 260 };

  function targetRowHeight() {
    var base = ROW_HEIGHTS[settings.density] || ROW_HEIGHTS.normal;
    /* Shorter rows on a phone: a 200px row leaves room for barely two images. */
    if (window.innerWidth < 560) return Math.round(base * 0.62);
    if (window.innerWidth < 900) return Math.round(base * 0.82);
    return base;
  }

  var grid = $("#imageGrid");

  function cardRatio(card) {
    var img = card.querySelector(".thumb");
    if (img && img.naturalWidth && img.naturalHeight) {
      return clampRatio(img.naturalWidth / img.naturalHeight);
    }
    return clampRatio(parseFloat(card.getAttribute("data-ratio")) || 1.5);
  }

  function clampRatio(value) {
    if (!isFinite(value) || value <= 0) return 1.5;
    /* Panoramas and thin strips would each eat a whole row. */
    return Math.max(0.5, Math.min(value, 3.2));
  }

  /* Pack cards into rows of a target height, then scale each row so it fills
     the container exactly — the standard justified-gallery algorithm. */
  function layoutGrid() {
    if (!grid) return;
    var cards = $$(".image-card", grid).filter(function (card) {
      return !card.classList.contains("img-broken");
    });
    if (!cards.length) return;

    var gap = 6;
    var containerWidth = grid.clientWidth;
    if (containerWidth < 100) return;

    var target = targetRowHeight();
    var row = [];
    var rowRatio = 0;

    function flush(isLastRow) {
      if (!row.length) return;
      var totalGap = gap * (row.length - 1);
      var height = (containerWidth - totalGap) / rowRatio;

      /* A short final row shouldn't be blown up to fill the width — cap it at
         the target height and let it end early, which is what looks right. */
      if (isLastRow && height > target * 1.4) height = target;

      row.forEach(function (entry, i) {
        var width = height * entry.ratio;
        if (!isLastRow && i === row.length - 1) {
          /* Absorb rounding into the last card so the row is flush. */
          var used = row.slice(0, -1).reduce(function (sum, e) {
            return sum + Math.round(height * e.ratio);
          }, 0);
          width = containerWidth - totalGap - used;
        }
        entry.card.style.width = Math.round(width) + "px";
        entry.card.style.height = Math.round(height) + "px";
      });
      row = [];
      rowRatio = 0;
    }

    cards.forEach(function (card, index) {
      var ratio = cardRatio(card);
      row.push({ card: card, ratio: ratio });
      rowRatio += ratio;
      var projected = (containerWidth - gap * (row.length - 1)) / rowRatio;
      if (projected <= target) flush(false);
      if (index === cards.length - 1) flush(true);
    });

    grid.classList.add("justified");
  }

  var layoutTimer = null;
  function relayout() {
    window.clearTimeout(layoutTimer);
    layoutTimer = window.setTimeout(layoutGrid, 60);
  }

  if (grid) {
    /* Each thumbnail's true aspect ratio is only known once it decodes; most
       pages never declare width/height, so relayout as they arrive. */
    $$(".image-card .thumb", grid).forEach(function (img) {
      if (img.complete && img.naturalWidth) {
        img.closest(".image-card").classList.add("loaded");
      } else {
        img.addEventListener("load", function () {
          img.closest(".image-card").classList.add("loaded");
          relayout();
        });
      }
    });
    /* A thumbnail whose origin blocks hot-linking may fire neither load nor
       error. Stop the shimmer after a few seconds so those cards settle into
       a plain placeholder rather than pulsing forever. */
    window.setTimeout(function () {
      $$(".image-card", grid).forEach(function (card) {
        card.classList.add("settled");
      });
    }, 6000);

    layoutGrid();
    window.addEventListener("resize", relayout);
    if ("ResizeObserver" in window) {
      new ResizeObserver(relayout).observe(grid);
    }
  }

  /* ------------------------------------------------------ infinite scroll */

  if (grid && $("#imgSentinel") && "IntersectionObserver" in window) {
    var sentinel = $("#imgSentinel");
    var loadingEl = $("#imgLoading");
    var nextPage = parseInt(grid.getAttribute("data-page") || "1", 10) + 1;
    var query = grid.getAttribute("data-query") || "";
    var sizeFilter = grid.getAttribute("data-size") || "";
    var loading = false;
    var exhausted = false;

    function appendImages(items) {
      var fragment = document.createDocumentFragment();
      items.forEach(function (item) {
        var card = document.createElement("a");
        card.className = "image-card";
        card.href = item.page_url || item.src;
        if (settings.newTab) {
          card.target = "_blank";
          card.rel = "noopener noreferrer";
        }
        var ratio = (item.width && item.height) ? item.width / item.height : 1.5;
        card.setAttribute("data-ratio", String(ratio));
        card.setAttribute("data-src", item.src);
        card.setAttribute("data-page", item.page_url || item.src);
        card.setAttribute("data-title", item.alt || item.page_title || item.host);
        card.setAttribute("data-host", item.host || "");
        card.setAttribute("data-w", item.width || 0);
        card.setAttribute("data-h", item.height || 0);

        var wrap = document.createElement("span");
        wrap.className = "thumb-wrap";
        var img = document.createElement("img");
        img.className = "thumb";
        img.loading = "lazy";
        img.decoding = "async";
        img.referrerPolicy = "no-referrer";
        img.alt = item.alt || "";
        img.src = item.src;
        img.addEventListener("load", function () {
          card.classList.add("loaded");
          relayout();
        });
        wrap.appendChild(img);
        card.appendChild(wrap);

        var cap = document.createElement("span");
        cap.className = "cap";
        var capIcon = document.createElement("img");
        capIcon.className = "cap-icon";
        capIcon.src = "/icon?h=" + encodeURIComponent(item.host || "");
        capIcon.alt = "";
        capIcon.width = 14; capIcon.height = 14;
        var capText = document.createElement("span");
        capText.className = "cap-text";
        var capTitle = document.createElement("span");
        capTitle.className = "cap-title";
        capTitle.textContent = item.alt || item.page_title || item.host || "";
        var capHost = document.createElement("span");
        capHost.className = "cap-host";
        capHost.textContent = item.host || "";
        capText.appendChild(capTitle);
        capText.appendChild(capHost);
        cap.appendChild(capIcon);
        cap.appendChild(capText);
        card.appendChild(cap);

        fragment.appendChild(card);
      });
      grid.appendChild(fragment);
      collectCards();
      relayout();
    }

    var imageObserver = new IntersectionObserver(function (entries) {
      if (!entries[0].isIntersecting || loading || exhausted || !query) return;
      loading = true;
      if (loadingEl) loadingEl.hidden = false;

      var url = "/api/images?q=" + encodeURIComponent(query) +
                "&page=" + nextPage + "&limit=60" +
                (sizeFilter ? "&size=" + encodeURIComponent(sizeFilter) : "");

      fetch(url)
        .then(function (response) { return response.json(); })
        .then(function (data) {
          var items = (data && data.results) || [];
          if (!items.length) {
            exhausted = true;
            imageObserver.disconnect();
          } else {
            appendImages(items);
            nextPage += 1;
          }
        })
        .catch(function () { exhausted = true; })
        .then(function () {
          loading = false;
          if (loadingEl) loadingEl.hidden = true;
        });
    }, { rootMargin: "600px" });

    imageObserver.observe(sentinel);
  }

  /* ============================================================ lightbox == */

  var lightbox = $("#lightbox");
  var cards = [];
  var collectCards = function () {};

  if (lightbox) {
    var lbImg     = $(".lb-img", lightbox);
    var lbTitle   = $(".lb-title", lightbox);
    var lbHost    = $(".lb-host", lightbox);
    var lbFavicon = $(".lb-favicon", lightbox);
    var lbVisit   = $(".lb-visit", lightbox);
    var lbOpen    = $(".lb-open", lightbox);
    var lbCopy    = $(".lb-copy", lightbox);
    var lbCount   = $(".lb-count", lightbox);
    var lbDims    = $(".lb-dims", lightbox);
    var similarGrid  = $(".lb-similar-grid", lightbox);
    var similarEmpty = $(".lb-similar-empty", lightbox);
    var current = -1;
    var lastFocus = null;

    collectCards = function () {
      cards = $$(".image-card").filter(function (card) {
        return !card.classList.contains("img-broken");
      });
    };
    collectCards();

    function setImage(item) {
      lbImg.classList.add("switching");
      window.setTimeout(function () {
        lbImg.src = item.src;
        lbImg.alt = item.title || "";
        lbTitle.textContent = item.title || item.host || "Image";
        lbHost.textContent = item.host || "";
        lbHost.href = item.page || item.src;
        lbVisit.href = item.page || item.src;
        lbOpen.href = item.src;
        if (lbCopy) lbCopy.setAttribute("data-copy", item.src);
        if (lbFavicon) {
          lbFavicon.src = "/icon?h=" + encodeURIComponent(item.host || "");
        }

        lbDims.innerHTML = "";
        function addDim(text) {
          if (!text) return;
          var span = document.createElement("span");
          span.className = "lb-dim";
          span.textContent = text;
          lbDims.appendChild(span);
        }
        if (item.w && item.h) addDim(item.w + " × " + item.h);
        var extension = (item.src.split("?")[0].split(".").pop() || "").toLowerCase();
        if (extension && extension.length <= 4) addDim(extension.toUpperCase());
        addDim(item.host);

        lbImg.classList.remove("switching");
      }, reduceMotion ? 0 : 110);
    }

    function loadSimilar(item) {
      similarGrid.innerHTML = "";
      similarEmpty.hidden = true;
      var url = "/api/similar?src=" + encodeURIComponent(item.src) +
                "&page=" + encodeURIComponent(item.page || "") +
                "&host=" + encodeURIComponent(item.host || "");

      fetch(url)
        .then(function (response) { return response.json(); })
        .then(function (list) {
          if (!list || !list.length) { similarEmpty.hidden = false; return; }
          list.forEach(function (entry, i) {
            var link = document.createElement("a");
            link.className = "lb-similar-item";
            link.style.setProperty("--si", i);
            link.href = entry.page || entry.src;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.title = entry.title || entry.host || "";

            var img = document.createElement("img");
            img.src = entry.src;
            img.alt = entry.title || "";
            img.loading = "lazy";
            img.decoding = "async";
            img.referrerPolicy = "no-referrer";
            link.appendChild(img);

            link.addEventListener("click", function (event) {
              if (event.metaKey || event.ctrlKey || event.button === 1) return;
              event.preventDefault();
              var next = {
                src: entry.src, page: entry.page, title: entry.title,
                host: entry.host, w: entry.width, h: entry.height
              };
              setImage(next);
              loadSimilar(next);
            });
            similarGrid.appendChild(link);
          });
        })
        .catch(function () { similarEmpty.hidden = false; });
    }

    function itemFromCard(card) {
      return {
        src:   card.getAttribute("data-src"),
        page:  card.getAttribute("data-page"),
        title: card.getAttribute("data-title") || "",
        host:  card.getAttribute("data-host") || "",
        w:     parseInt(card.getAttribute("data-w") || "0", 10),
        h:     parseInt(card.getAttribute("data-h") || "0", 10)
      };
    }

    function preload(index) {
      var card = cards[index];
      if (!card) return;
      var image = new Image();
      image.referrerPolicy = "no-referrer";
      image.src = card.getAttribute("data-src");
    }

    function openAt(index) {
      var card = cards[index];
      if (!card) return;
      if (current < 0) lastFocus = document.activeElement;

      cards.forEach(function (c) { c.classList.remove("is-active"); });
      card.classList.add("is-active");
      current = index;

      var item = itemFromCard(card);
      /* The card knows the declared size; the decoded image knows the truth. */
      var thumb = card.querySelector(".thumb");
      if (thumb && thumb.naturalWidth) {
        item.w = thumb.naturalWidth;
        item.h = thumb.naturalHeight;
      }
      setImage(item);
      loadSimilar(item);
      if (lbCount) lbCount.textContent = (index + 1) + " of " + cards.length;

      lightbox.hidden = false;
      void lightbox.offsetWidth;          /* force a reflow so the transition runs */
      lightbox.classList.add("open");
      lightbox.setAttribute("aria-hidden", "false");
      document.body.classList.add("lb-open");

      preload(index + 1);
      preload(index - 1);
      card.scrollIntoView({ block: "nearest", behavior: reduceMotion ? "auto" : "smooth" });
    }

    function close() {
      lightbox.classList.remove("open");
      lightbox.setAttribute("aria-hidden", "true");
      document.body.classList.remove("lb-open");
      cards.forEach(function (c) { c.classList.remove("is-active"); });
      current = -1;
      if (lastFocus && lastFocus.focus) lastFocus.focus();
      window.setTimeout(function () {
        if (!lightbox.classList.contains("open")) {
          lightbox.hidden = true;
          lbImg.src = "";
          similarGrid.innerHTML = "";
        }
      }, 340);
    }

    function step(delta) {
      if (current < 0) return;
      var next = current + delta;
      if (next >= 0 && next < cards.length) openAt(next);
    }

    document.addEventListener("click", function (event) {
      var card = event.target.closest(".image-card");
      if (!card) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.button === 1) return;
      event.preventDefault();
      collectCards();
      openAt(cards.indexOf(card));
    });

    lightbox.addEventListener("click", function (event) {
      if (event.target.closest("[data-close], .lb-close")) { close(); return; }
      if (event.target.closest(".lb-prev, .lb-step.prev")) { step(-1); return; }
      if (event.target.closest(".lb-next, .lb-step.next")) { step(1); }
    });

    /* Swipe between images on touch devices. */
    var touchStartX = 0, touchStartY = 0;
    lightbox.addEventListener("touchstart", function (event) {
      touchStartX = event.changedTouches[0].clientX;
      touchStartY = event.changedTouches[0].clientY;
    }, { passive: true });

    lightbox.addEventListener("touchend", function (event) {
      var dx = event.changedTouches[0].clientX - touchStartX;
      var dy = event.changedTouches[0].clientY - touchStartY;
      if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5) {
        step(dx < 0 ? 1 : -1);
      } else if (dy > 90 && Math.abs(dy) > Math.abs(dx) * 1.5) {
        close();                          /* swipe down to dismiss the sheet */
      }
    }, { passive: true });

    document.addEventListener("keydown", function (event) {
      if (lightbox.hidden || !lightbox.classList.contains("open")) return;
      if (event.key === "Escape") { event.preventDefault(); close(); }
      else if (event.key === "ArrowRight") { event.preventDefault(); step(1); }
      else if (event.key === "ArrowLeft") { event.preventDefault(); step(-1); }
    });
  }

  /* ========================================================= video modal == */

  var videoModal = $("#videoModal");
  if (videoModal) {
    var frame = $("#videoModalFrame");

    function openVideo(embedUrl) {
      if (!embedUrl) return;
      var separator = embedUrl.indexOf("?") >= 0 ? "&" : "?";
      /* Muted autoplay is the only kind browsers permit without a gesture on
         the destination; the viewer can unmute in the player. */
      frame.src = embedUrl + separator + "autoplay=1&mute=1";
      videoModal.hidden = false;
      void videoModal.offsetWidth;
      videoModal.classList.add("open");
      videoModal.setAttribute("aria-hidden", "false");
      document.body.style.overflow = "hidden";
    }

    function closeVideo() {
      videoModal.classList.remove("open");
      videoModal.setAttribute("aria-hidden", "true");
      document.body.style.overflow = "";
      window.setTimeout(function () {
        if (!videoModal.classList.contains("open")) {
          videoModal.hidden = true;
          frame.src = "";               /* stop playback */
        }
      }, 300);
    }

    document.addEventListener("click", function (event) {
      var trigger = event.target.closest("[data-embed-trigger]");
      if (trigger) {
        if (event.metaKey || event.ctrlKey || event.button === 1) return;
        var card = trigger.closest("[data-embed]");
        var url = card && card.getAttribute("data-embed");
        if (url) { event.preventDefault(); openVideo(url); }
        return;
      }
      if (event.target.closest("[data-vm-close]")) closeVideo();
    });

    document.addEventListener("keydown", function (event) {
      if (videoModal.hidden || !videoModal.classList.contains("open")) return;
      if (event.key === "Escape") { event.preventDefault(); closeVideo(); }
    });
  }

  /* ==================================================== keyboard shortcuts */

  var shortcuts = $("#shortcuts");

  function openShortcuts() {
    if (!shortcuts) return;
    shortcuts.hidden = false;
    void shortcuts.offsetWidth;
    shortcuts.classList.add("open");
  }

  function closeShortcuts() {
    if (!shortcuts) return;
    shortcuts.classList.remove("open");
    window.setTimeout(function () {
      if (!shortcuts.classList.contains("open")) shortcuts.hidden = true;
    }, 240);
  }

  if (shortcuts) {
    shortcuts.addEventListener("click", function (event) {
      if (event.target === shortcuts || event.target.closest("[data-sc-close]")) {
        closeShortcuts();
      }
    });
  }

  function isTyping() {
    var el = document.activeElement;
    return el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);
  }

  var resultCards = $$(".result");
  var cursor = -1;

  function moveCursor(delta) {
    if (!resultCards.length) return;
    if (cursor >= 0 && resultCards[cursor]) {
      resultCards[cursor].classList.remove("is-current");
    }
    cursor = Math.max(0, Math.min(resultCards.length - 1, cursor + delta));
    var card = resultCards[cursor];
    card.classList.add("is-current");
    card.scrollIntoView({ block: "center", behavior: reduceMotion ? "auto" : "smooth" });
    var link = card.querySelector("h3 a");
    if (link) link.focus({ preventScroll: true });
  }

  document.addEventListener("keydown", function (event) {
    if (event.metaKey || event.ctrlKey || event.altKey) return;

    if (event.key === "Escape") {
      if (shortcuts && !shortcuts.hidden) { closeShortcuts(); return; }
      hideSuggestions();
      return;
    }

    if (isTyping()) return;

    if (event.key === "/") {
      event.preventDefault();
      var input = $('.searchbox input[name="q"]');
      if (input) { input.focus(); input.select(); }
      return;
    }
    if (event.key === "?") { event.preventDefault(); openShortcuts(); return; }
    if (event.key === "j") { event.preventDefault(); moveCursor(cursor < 0 ? 0 : 1); return; }
    if (event.key === "k") { event.preventDefault(); moveCursor(-1); return; }

    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      var rel = event.key === "ArrowLeft" ? "prev" : "next";
      var pageLink = $('.pager-btn[rel="' + rel + '"]:not(.disabled)');
      if (pageLink) { event.preventDefault(); window.location.href = pageLink.href; }
      return;
    }

    if (/^[1-4]$/.test(event.key)) {
      var tabs = $$(".tab");
      var tab = tabs[parseInt(event.key, 10) - 1];
      if (tab) { event.preventDefault(); window.location.href = tab.href; }
    }
  });

  /* ======================================================= settings page == */

  $$("[data-setting]").forEach(function (control) {
    var key = control.getAttribute("data-setting");
    if (control.type === "checkbox") {
      control.checked = !!settings[key];
      control.addEventListener("change", function () {
        settings[key] = control.checked;
        writeSettings(settings);
        toast("Saved");
      });
    } else {
      control.value = settings[key];
      control.addEventListener("change", function () {
        settings[key] = control.value;
        writeSettings(settings);
        toast("Saved");
        relayout();
      });
    }
  });

  var clearLocal = $("#clearLocal");
  if (clearLocal) {
    clearLocal.addEventListener("click", function () {
      try {
        window.localStorage.removeItem(SETTINGS_KEY);
        window.localStorage.removeItem(RECENT_KEY);
      } catch (err) { /* ignore */ }
      toast("Local data cleared");
      window.setTimeout(function () { window.location.reload(); }, 600);
    });
  }

  /* ================================================== advanced search form */

  var advForm = $("#advForm");
  if (advForm) {
    var output = $("#adv-q");

    function buildQuery() {
      var parts = [];
      function value(name) {
        var field = advForm.querySelector('[data-adv="' + name + '"]');
        return field ? field.value.trim() : "";
      }
      if (value("all")) parts.push(value("all"));
      if (value("phrase")) parts.push('"' + value("phrase").replace(/"/g, "") + '"');
      value("none").split(/\s+/).forEach(function (word) {
        if (word) parts.push("-" + word);
      });
      if (value("site")) parts.push("site:" + value("site"));
      value("intitle").split(/\s+/).forEach(function (word) {
        if (word) parts.push("intitle:" + word);
      });
      value("inurl").split(/\s+/).forEach(function (word) {
        if (word) parts.push("inurl:" + word);
      });
      output.value = parts.join(" ");
    }

    $$("[data-adv]", advForm).forEach(function (field) {
      field.addEventListener("input", buildQuery);
    });
    buildQuery();
  }

  /* ===================================================== document scrollspy */

  var tocLinks = $$(".doc-toc a");
  if (tocLinks.length && "IntersectionObserver" in window) {
    var sections = $$(".doc-section");
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        tocLinks.forEach(function (link) {
          link.classList.toggle(
            "active",
            link.getAttribute("href") === "#" + entry.target.id
          );
        });
      });
    }, { rootMargin: "-15% 0px -70% 0px" });
    sections.forEach(function (section) { spy.observe(section); });
  }

  /* ================================================ shared Web Audio ctx == */

  var audioCtx = null;
  function ac() {
    if (!audioCtx) {
      var Ctor = window.AudioContext || window.webkitAudioContext;
      if (Ctor) audioCtx = new Ctor();
    }
    if (audioCtx && audioCtx.state === "suspended") audioCtx.resume();
    return audioCtx;
  }

  function beep(freq, duration, when, gainValue) {
    var ctx = ac();
    if (!ctx) return;
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.frequency.value = freq;
    osc.type = "sine";
    gain.gain.setValueAtTime(gainValue || 0.3, when);
    gain.gain.exponentialRampToValueAtTime(0.0001, when + duration);
    osc.connect(gain).connect(ctx.destination);
    osc.start(when);
    osc.stop(when + duration);
  }

  /* ==================================================== morse playback ==== */

  document.addEventListener("click", function (event) {
    var button = event.target.closest(".morse-play");
    if (!button) return;
    event.preventDefault();
    var morse = button.getAttribute("data-morse") || "";
    var ctx = ac();
    if (!ctx) { toast("Audio unavailable"); return; }
    var unit = 0.08;                       /* one dot */
    var t = ctx.currentTime + 0.05;
    button.classList.add("playing");
    for (var i = 0; i < morse.length; i++) {
      var ch = morse[i];
      if (ch === ".") { beep(660, unit, t, 0.3); t += unit * 2; }
      else if (ch === "-") { beep(660, unit * 3, t, 0.3); t += unit * 4; }
      else if (ch === " ") { t += unit * 2; }
      else if (ch === "/") { t += unit * 4; }
    }
    window.setTimeout(function () { button.classList.remove("playing"); },
      (t - ctx.currentTime) * 1000);
  });

  /* ==================================================== stopwatch ========= */

  $$('[data-widget="stopwatch"]').forEach(function (root) {
    var display = $("[data-sw-display]", root);
    var lapsEl = $("[data-sw-laps]", root);
    var startBtn = $("[data-sw-start]", root);
    var lapBtn = $("[data-sw-lap]", root);
    var resetBtn = $("[data-sw-reset]", root);
    var setEl = $("[data-sw-set]", root);
    var minInput = $("[data-sw-min]", root);
    var secInput = $("[data-sw-sec]", root);
    var mode = "stopwatch";
    var running = false, raf = null, startTime = 0, elapsed = 0;
    var lapCount = 0, lastLap = 0, target = 0;
    var playIcon = '<svg width="15" height="15" viewBox="0 0 24 24" ' +
      'fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
    var pauseIcon = '<svg width="15" height="15" viewBox="0 0 24 24" ' +
      'fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/>' +
      '<rect x="14" y="5" width="4" height="14" rx="1"/></svg>';

    function fmt(ms) {
      var neg = ms < 0;
      ms = Math.abs(ms);
      var m = Math.floor(ms / 60000);
      var s = Math.floor((ms % 60000) / 1000);
      var cs = Math.floor((ms % 1000) / 10);
      return (neg ? "-" : "") + pad(m) + ":" + pad(s) +
             '.<small>' + pad(cs) + "</small>";
    }
    function pad(n) { return (n < 10 ? "0" : "") + n; }

    function tick() {
      var now = performance.now();
      if (mode === "stopwatch") {
        display.innerHTML = fmt(elapsed + (now - startTime));
      } else {
        var remaining = target - (elapsed + (now - startTime));
        display.innerHTML = fmt(Math.max(0, remaining));
        if (remaining <= 0) { finishTimer(); return; }
      }
      raf = window.requestAnimationFrame(tick);
    }

    function finishTimer() {
      stop();
      display.innerHTML = fmt(0);
      var ctx = ac();
      if (ctx) {
        var t = ctx.currentTime;
        for (var i = 0; i < 4; i++) beep(880, 0.15, t + i * 0.2, 0.4);
      }
      toast("Timer finished");
    }

    function start() {
      if (mode === "timer" && elapsed === 0) {
        target = (parseInt(minInput.value, 10) || 0) * 60000 +
                 (parseInt(secInput.value, 10) || 0) * 1000;
        if (target <= 0) { toast("Set a time first"); return; }
      }
      running = true;
      startTime = performance.now();
      startBtn.innerHTML = pauseIcon + " Pause";
      raf = window.requestAnimationFrame(tick);
    }
    function stop() {
      running = false;
      if (raf) window.cancelAnimationFrame(raf);
      elapsed += performance.now() - startTime;
      startBtn.innerHTML = playIcon + " " + (mode === "timer" && elapsed > 0
        ? "Resume" : "Start");
    }
    function reset() {
      stop();
      elapsed = 0; lapCount = 0; lastLap = 0;
      display.innerHTML = mode === "timer"
        ? fmt((parseInt(minInput.value, 10) || 0) * 60000 +
              (parseInt(secInput.value, 10) || 0) * 1000)
        : fmt(0);
      lapsEl.innerHTML = "";
      startBtn.innerHTML = playIcon + " Start";
    }

    startBtn.addEventListener("click", function () {
      running ? stop() : start();
    });
    resetBtn.addEventListener("click", reset);
    lapBtn.addEventListener("click", function () {
      if (mode !== "stopwatch" || !running) return;
      var total = elapsed + (running ? performance.now() - startTime : 0);
      lapCount += 1;
      var split = total - lastLap;
      lastLap = total;
      var li = document.createElement("li");
      li.innerHTML = "<span>Lap " + lapCount + "</span><span>" +
        fmt(split).replace(/<\/?small>/g, "") + "</span>";
      lapsEl.insertBefore(li, lapsEl.firstChild);
    });

    $$("[data-sw-mode]", root).forEach(function (tab) {
      tab.addEventListener("click", function () {
        $$("[data-sw-mode]", root).forEach(function (t) {
          t.classList.remove("active");
        });
        tab.classList.add("active");
        mode = tab.getAttribute("data-sw-mode");
        setEl.hidden = mode !== "timer";
        lapBtn.style.display = mode === "timer" ? "none" : "";
        reset();
      });
    });
  });

  /* ==================================================== metronome ========= */

  $$('[data-widget="metronome"]').forEach(function (root) {
    var slider = $("[data-metro-slider]", root);
    var display = $("[data-metro-display]", root);
    var beatEl = $("[data-metro-beat]", root);
    var toggle = $("[data-metro-toggle]", root);
    var beatsSel = $("[data-metro-beats]", root);
    var bpm = parseInt(slider.value, 10);
    var playing = false, nextNoteTime = 0, beatInBar = 0, schedTimer = null;

    function setBpm(value) {
      bpm = Math.max(40, Math.min(240, value));
      slider.value = bpm;
      display.textContent = bpm;
    }

    function scheduler() {
      var ctx = ac();
      if (!ctx) return;
      var beatsPerBar = parseInt(beatsSel.value, 10) || 4;
      while (nextNoteTime < ctx.currentTime + 0.1) {
        var accent = beatInBar % beatsPerBar === 0;
        beep(accent ? 1320 : 880, 0.05, nextNoteTime, accent ? 0.5 : 0.3);
        var visualDelay = (nextNoteTime - ctx.currentTime) * 1000;
        (function (isAccent) {
          window.setTimeout(function () {
            beatEl.classList.add("tick");
            beatEl.classList.toggle("accent", isAccent);
            window.setTimeout(function () {
              beatEl.classList.remove("tick");
            }, 90);
          }, Math.max(0, visualDelay));
        })(accent);
        nextNoteTime += 60 / bpm;
        beatInBar += 1;
      }
      schedTimer = window.setTimeout(scheduler, 25);
    }

    function startMetro() {
      var ctx = ac();
      if (!ctx) { toast("Audio unavailable"); return; }
      playing = true;
      beatInBar = 0;
      nextNoteTime = ctx.currentTime + 0.05;
      toggle.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" ' +
        'fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/>' +
        '<rect x="14" y="5" width="4" height="14" rx="1"/></svg> Stop';
      scheduler();
    }
    function stopMetro() {
      playing = false;
      window.clearTimeout(schedTimer);
      beatEl.classList.remove("tick");
      toggle.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" ' +
        'fill="currentColor"><path d="M8 5v14l11-7z"/></svg> Start';
    }

    slider.addEventListener("input", function () { setBpm(parseInt(slider.value, 10)); });
    toggle.addEventListener("click", function () { playing ? stopMetro() : startMetro(); });
    var up = $("[data-metro-up]", root), down = $("[data-metro-down]", root);
    if (up) up.addEventListener("click", function () { setBpm(bpm + 5); });
    if (down) down.addEventListener("click", function () { setBpm(bpm - 5); });

    var tapBtn = $("[data-metro-tap]", root), taps = [];
    if (tapBtn) {
      tapBtn.addEventListener("click", function () {
        var now = performance.now();
        taps = taps.filter(function (t) { return now - t < 2000; });
        taps.push(now);
        if (taps.length >= 2) {
          var intervals = [];
          for (var i = 1; i < taps.length; i++) intervals.push(taps[i] - taps[i - 1]);
          var avg = intervals.reduce(function (a, b) { return a + b; }, 0) / intervals.length;
          setBpm(Math.round(60000 / avg));
        }
      });
    }
  });

  /* ==================================================== ambient noise ===== */

  $$('[data-widget="noise"]').forEach(function (root) {
    var controls = $("[data-noise-controls]", root);
    var stopBtn = $("[data-noise-stop]", root);
    var volInput = $("[data-noise-vol]", root);
    var current = null, currentKey = null, autoStop = null;

    function makeNoiseBuffer(ctx, kind) {
      var len = ctx.sampleRate * 2;
      var buffer = ctx.createBuffer(1, len, ctx.sampleRate);
      var out = buffer.getChannelData(0);
      var last = 0, b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
      for (var i = 0; i < len; i++) {
        var white = Math.random() * 2 - 1;
        if (kind === "pink" || kind === "rain") {
          b0 = 0.99886 * b0 + white * 0.0555179;
          b1 = 0.99332 * b1 + white * 0.0750759;
          b2 = 0.96900 * b2 + white * 0.1538520;
          b3 = 0.86650 * b3 + white * 0.3104856;
          b4 = 0.55000 * b4 + white * 0.5329522;
          b5 = -0.7616 * b5 - white * 0.0168980;
          out[i] = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + white * 0.5362) * 0.11;
          b6 = white * 0.115926;
        } else if (kind === "brown" || kind === "ocean") {
          last = (last + 0.02 * white) / 1.02;
          out[i] = last * 3.5;
        } else {
          out[i] = white * 0.5;
        }
      }
      return buffer;
    }

    function play(kind, btn) {
      var ctx = ac();
      if (!ctx) { toast("Audio unavailable"); return; }
      stopNoise();
      var src = ctx.createBufferSource();
      src.buffer = makeNoiseBuffer(ctx, kind);
      src.loop = true;
      var gain = ctx.createGain();
      gain.gain.value = (parseInt(volInput.value, 10) || 50) / 100 * 0.6;
      var node = src;
      /* Rain and ocean get gentle shaping: a lowpass, and for ocean a slow
         swell in volume. */
      if (kind === "rain" || kind === "ocean") {
        var filter = ctx.createBiquadFilter();
        filter.type = "lowpass";
        filter.frequency.value = kind === "ocean" ? 500 : 2200;
        node.connect(filter);
        node = filter;
      }
      node.connect(gain).connect(ctx.destination);
      src.start();
      if (kind === "ocean") {
        var lfo = ctx.createOscillator();
        var lfoGain = ctx.createGain();
        lfo.frequency.value = 0.12;
        lfoGain.gain.value = gain.gain.value * 0.6;
        lfo.connect(lfoGain).connect(gain.gain);
        lfo.start();
        current = { src: src, gain: gain, lfo: lfo };
      } else {
        current = { src: src, gain: gain };
      }
      currentKey = kind;
      controls.hidden = false;
      $$(".noise-btn", root).forEach(function (b) { b.classList.remove("playing"); });
      btn.classList.add("playing");
      /* Safety: stop after an hour so a forgotten tab doesn't hum forever. */
      window.clearTimeout(autoStop);
      autoStop = window.setTimeout(stopNoise, 3600000);
    }

    function stopNoise() {
      if (current) {
        try { current.src.stop(); } catch (e) {}
        if (current.lfo) try { current.lfo.stop(); } catch (e) {}
        current = null; currentKey = null;
      }
      controls.hidden = true;
      $$(".noise-btn", root).forEach(function (b) { b.classList.remove("playing"); });
      window.clearTimeout(autoStop);
    }

    $$(".noise-btn", root).forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (currentKey === btn.getAttribute("data-noise")) { stopNoise(); return; }
        play(btn.getAttribute("data-noise"), btn);
      });
    });
    stopBtn.addEventListener("click", stopNoise);
    volInput.addEventListener("input", function () {
      if (current) current.gain.gain.value = (parseInt(volInput.value, 10) || 50) / 100 * 0.6;
    });
  });

  /* ==================================================== font previewer ==== */

  $$('[data-widget="font-preview"]').forEach(function (root) {
    var input = $("[data-font-input]", root);
    var preview = $("[data-font-preview]", root);
    var family = $("[data-font-family]", root);
    var size = $("[data-font-size]", root);
    var bold = $("[data-font-bold]", root);
    var italic = $("[data-font-italic]", root);

    function apply() {
      preview.textContent = input.value || "The quick brown fox";
      preview.style.fontFamily = family.value;
      preview.style.fontSize = size.value + "px";
      preview.style.fontWeight = bold.getAttribute("aria-pressed") === "true" ? "700" : "400";
      preview.style.fontStyle = italic.getAttribute("aria-pressed") === "true" ? "italic" : "normal";
    }
    input.addEventListener("input", apply);
    family.addEventListener("change", apply);
    size.addEventListener("input", apply);
    [bold, italic].forEach(function (btn) {
      btn.addEventListener("click", function () {
        btn.setAttribute("aria-pressed",
          btn.getAttribute("aria-pressed") === "true" ? "false" : "true");
        apply();
      });
    });
  });

  /* ==================================================== recipe converter == */

  $$("[data-recipe]").forEach(function (root) {
    var input = $("[data-recipe-input]", root);
    var factor = $("[data-recipe-factor]", root);
    var fromServ = $("[data-recipe-from]", root);
    var toServ = $("[data-recipe-to]", root);
    var output = $("[data-recipe-output]", root);
    var subs;
    try { subs = JSON.parse(root.getAttribute("data-subs")); }
    catch (e) { subs = {}; }
    var activeDiets = {};

    /* A compact port of the server's fraction-aware scaler. */
    var UNI = { "½": ".5", "⅓": " 1/3", "⅔": " 2/3", "¼": ".25", "¾": ".75",
      "⅛": ".125", "⅜": ".375", "⅝": ".625", "⅞": ".875" };
    function gcd(a, b) { return b ? gcd(b, a % b) : a; }
    function toFraction(str) {
      str = str.trim();
      if (str.indexOf(" ") >= 0) {
        var p = str.split(/\s+/);
        var f = p[1].split("/");
        return parseInt(p[0], 10) + parseInt(f[0], 10) / parseInt(f[1], 10);
      }
      if (str.indexOf("/") >= 0) {
        var q = str.split("/"); return parseInt(q[0], 10) / parseInt(q[1], 10);
      }
      return parseFloat(str);
    }
    function fmt(value) {
      if (value === 0) return "0";
      var whole = Math.floor(value);
      var frac = value - whole;
      if (frac < 0.02) return String(whole);
      /* snap to sixteenths */
      var num = Math.round(frac * 16), den = 16;
      var g = gcd(num, den); num /= g; den /= g;
      if (num === 0) return String(whole);
      if (num === den) return String(whole + 1);
      var f = num + "/" + den;
      return whole ? whole + " " + f : f;
    }
    function scaleLine(line, mult) {
      var norm = line;
      for (var k in UNI) norm = norm.split(k).join(UNI[k]);
      norm = norm.replace(/(\d)\s+\./g, "$1.");
      var m = norm.match(/^\s*(\d+\s+\d+\/\d+|\d+\/\d+|\d*\.\d+|\d+(?:\.\d+)?)\s*(.*)$/);
      if (!m) return line.trim();
      var q = toFraction(m[1]);
      if (isNaN(q)) return line.trim();
      var rest = m[2];
      for (var diet in activeDiets) {
        if (!activeDiets[diet]) continue;
        (subs[diet] || []).forEach(function (rule) {
          rest = rest.replace(new RegExp(rule[0], "gi"), rule[1]);
        });
      }
      return (fmt(q * mult) + " " + rest).trim();
    }

    function currentFactor() {
      var f = parseFloat(factor.value);
      if (f && f > 0) return f;
      var a = parseInt(fromServ.value, 10), b = parseInt(toServ.value, 10);
      return (a > 0 && b > 0) ? b / a : 1;
    }

    function render() {
      var mult = currentFactor();
      var lines = input.value.split("\n");
      output.innerHTML = "";
      lines.forEach(function (line) {
        if (!line.trim()) return;
        var div = document.createElement("div");
        div.className = "recipe-line";
        div.textContent = scaleLine(line, mult);
        output.appendChild(div);
      });
    }

    input.addEventListener("input", render);
    factor.addEventListener("input", function () { render(); });
    [fromServ, toServ].forEach(function (el) {
      el.addEventListener("input", function () { factor.value = ""; render(); });
    });
    $$("[data-recipe-preset]", root).forEach(function (btn) {
      btn.addEventListener("click", function () {
        factor.value = btn.getAttribute("data-recipe-preset");
        render();
      });
    });
    $$("[data-diet]", root).forEach(function (btn) {
      btn.addEventListener("click", function () {
        var diet = btn.getAttribute("data-diet");
        activeDiets[diet] = !activeDiets[diet];
        btn.setAttribute("aria-pressed", activeDiets[diet] ? "true" : "false");
        render();
      });
    });
    render();
  });

  /* ==================================================== meeting planner === */

  $$("[data-meeting]").forEach(function (root) {
    var grid = $("[data-meet-grid]", root);
    var addBtn = $("[data-meet-add]", root);
    var addInput = $("[data-meet-input]", root);
    var columns;
    try { columns = JSON.parse(root.getAttribute("data-columns")); }
    catch (e) { columns = []; }

    var CITY_TO_ZONE = {
      "tokyo": ["Asia/Tokyo", 9], "london": ["Europe/London", 1],
      "new york": ["America/New_York", -4], "los angeles": ["America/Los_Angeles", -7],
      "paris": ["Europe/Paris", 2], "berlin": ["Europe/Berlin", 2],
      "sydney": ["Australia/Sydney", 10], "dubai": ["Asia/Dubai", 4],
      "singapore": ["Asia/Singapore", 8], "mumbai": ["Asia/Kolkata", 5.5],
      "chicago": ["America/Chicago", -5], "sao paulo": ["America/Sao_Paulo", -3],
      "moscow": ["Europe/Moscow", 3], "beijing": ["Asia/Shanghai", 8],
      "seoul": ["Asia/Seoul", 9], "toronto": ["America/Toronto", -4]
    };

    function render() {
      /* Base row: UTC hours 0..23. Each column shows its local hour, shaded
         green when it's daytime everywhere (the overlap). */
      var html = '<div class="meet-row meet-head"><span class="meet-label">UTC</span>';
      for (var h = 0; h < 24; h++) html += '<span class="meet-hr">' + h + '</span>';
      html += '</div>';

      columns.forEach(function (col, ci) {
        html += '<div class="meet-row"><span class="meet-label">' +
          col.label + ' <small>' + col.abbrev + '</small>' +
          (columns.length > 1 ? ' <button class="meet-rm" data-rm="' + ci +
           '" aria-label="Remove">×</button>' : '') + '</span>';
        for (var h = 0; h < 24; h++) {
          var local = ((h + Math.floor(col.offset_hours)) % 24 + 24) % 24;
          var daytime = local >= 8 && local < 20;
          var overlap = columns.every(function (c) {
            var l = ((h + Math.floor(c.offset_hours)) % 24 + 24) % 24;
            return l >= 8 && l < 20;
          });
          var cls = "meet-cell" + (overlap ? " overlap" : (daytime ? " day" : ""));
          html += '<span class="' + cls + '">' + local + '</span>';
        }
        html += '</div>';
      });
      grid.innerHTML = html;

      $$("[data-rm]", grid).forEach(function (b) {
        b.addEventListener("click", function () {
          columns.splice(parseInt(b.getAttribute("data-rm"), 10), 1);
          render();
        });
      });
    }

    function add() {
      var city = addInput.value.trim().toLowerCase();
      var z = CITY_TO_ZONE[city];
      if (!z) { toast("Try a major city name"); return; }
      if (columns.length >= 6) { toast("Up to 6 zones"); return; }
      columns.push({ zone: z[0], label: city.replace(/\b\w/g, function (c) {
        return c.toUpperCase(); }), offset_hours: z[1], abbrev: "" });
      addInput.value = "";
      render();
    }

    if (addBtn) addBtn.addEventListener("click", add);
    if (addInput) addInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); add(); }
    });
    render();
  });

  /* ================================================ scale of universe ===== */

  $$("[data-universe]").forEach(function (root) {
    var slider = $("[data-uni-slider]", root);
    var emoji = $("[data-uni-emoji]", root);
    var nameEl = $("[data-uni-name]", root);
    var sizeEl = $("[data-uni-size]", root);
    var blurb = $("[data-uni-blurb]", root);
    var neighbours = $("[data-uni-neighbours]", root);
    var objects;
    try { objects = JSON.parse(root.getAttribute("data-objects")); }
    catch (err) { return; }

    function sizeLabel(log) {
      var metres = Math.pow(10, log);
      var units = [
        [1e-15, "femtometres", 1e15], [1e-12, "picometres", 1e12],
        [1e-9, "nanometres", 1e9], [1e-6, "micrometres", 1e6],
        [1e-3, "millimetres", 1e3], [1, "metres", 1],
        [1e3, "kilometres", 1e-3], [9.461e15, "light-years", 1 / 9.461e15]
      ];
      var chosen = units[0];
      for (var i = 0; i < units.length; i++) {
        if (metres >= units[i][0]) chosen = units[i];
      }
      var value = metres * chosen[2];
      var text = value >= 100 ? value.toPrecision(3) : value.toFixed(2);
      var num = parseFloat(text);
      var unit = chosen[1];
      if (num === 1) unit = unit.replace(/s$/, "");   /* 1 metre, not 1 metres */
      return num.toLocaleString() + " " + unit;
    }

    function nearest(log) {
      var best = objects[0], bestD = Infinity, idx = 0;
      objects.forEach(function (o, i) {
        var d = Math.abs(o.log - log);
        if (d < bestD) { bestD = d; best = o; idx = i; }
      });
      return idx;
    }

    function render(log) {
      var idx = nearest(log);
      var o = objects[idx];
      emoji.textContent = o.emoji;
      nameEl.textContent = o.name;
      sizeEl.textContent = sizeLabel(o.log);
      blurb.textContent = o.blurb;
      /* scale the emoji a little by how close we are, for a sense of zoom */
      var frac = 1 - Math.min(1, Math.abs(o.log - log));
      emoji.style.transform = "scale(" + (0.85 + frac * 0.35) + ")";

      neighbours.innerHTML = "";
      [idx - 1, idx + 1].forEach(function (n) {
        if (n < 0 || n >= objects.length) return;
        var nb = objects[n];
        var b = document.createElement("button");
        b.className = "uni-neighbour";
        b.innerHTML = '<span>' + nb.emoji + '</span>' + nb.name;
        b.addEventListener("click", function () {
          slider.value = nb.log; render(nb.log);
        });
        neighbours.appendChild(b);
      });
    }

    slider.addEventListener("input", function () {
      render(parseFloat(slider.value));
    });
    render(0);
  });

  /* ==================================================== carry-on checker == */

  $$("[data-luggage]").forEach(function (root) {
    var select = $("[data-lug-select]", root);
    var result = $("[data-lug-result]", root);

    select.addEventListener("change", function () {
      var opt = select.options[select.selectedIndex];
      if (!opt.value) { result.hidden = true; return; }
      function row(label, value) {
        return value ? '<dt>' + label + '</dt><dd>' + value + '</dd>' : '';
      }
      result.innerHTML = '<div class="lug-dims">' +
        opt.getAttribute("data-dims") + '</div>' +
        '<dl class="answer-rows">' +
        row("In inches", opt.getAttribute("data-in")) +
        row("Weight", opt.getAttribute("data-weight")) +
        row("Personal item", opt.getAttribute("data-personal")) +
        '</dl>';
      result.hidden = false;
    });
  });

  /* ==================================================== colour picker ===== */

  $$("[data-cp]").forEach(function (root) {
    var input = $("[data-cp-input]", root);
    var preview = $("[data-cp-preview]", root);
    var rows = $("[data-cp-rows]", root);
    var shades = $("[data-cp-shades]", root);

    function hexToRgb(hex) {
      var n = parseInt(hex.slice(1), 16);
      return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    }
    function rgbToHsl(r, g, b) {
      r /= 255; g /= 255; b /= 255;
      var max = Math.max(r, g, b), min = Math.min(r, g, b);
      var h = 0, s = 0, l = (max + min) / 2;
      if (max !== min) {
        var d = max - min;
        s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
        if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
        else if (max === g) h = (b - r) / d + 2;
        else h = (r - g) / d + 4;
        h /= 6;
      }
      return [Math.round(h * 360), Math.round(s * 100), Math.round(l * 100)];
    }
    function rgbToHsv(r, g, b) {
      r /= 255; g /= 255; b /= 255;
      var max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
      var h = 0, s = max === 0 ? 0 : d / max, v = max;
      if (max !== min) {
        if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
        else if (max === g) h = (b - r) / d + 2;
        else h = (r - g) / d + 4;
        h /= 6;
      }
      return [Math.round(h * 360), Math.round(s * 100), Math.round(v * 100)];
    }
    function rgbToCmyk(r, g, b) {
      var k = 1 - Math.max(r, g, b) / 255;
      if (k >= 1) return [0, 0, 0, 100];
      return [
        Math.round((1 - r / 255 - k) / (1 - k) * 100),
        Math.round((1 - g / 255 - k) / (1 - k) * 100),
        Math.round((1 - b / 255 - k) / (1 - k) * 100),
        Math.round(k * 100)
      ];
    }
    function hslToHex(h, s, l) {
      s /= 100; l /= 100;
      var c = (1 - Math.abs(2 * l - 1)) * s;
      var x = c * (1 - Math.abs((h / 60) % 2 - 1));
      var m = l - c / 2, r = 0, g = 0, b = 0;
      if (h < 60) { r = c; g = x; }
      else if (h < 120) { r = x; g = c; }
      else if (h < 180) { g = c; b = x; }
      else if (h < 240) { g = x; b = c; }
      else if (h < 300) { r = x; b = c; }
      else { r = c; b = x; }
      return "#" + [r, g, b].map(function (v) {
        return ("0" + Math.round((v + m) * 255).toString(16)).slice(-2);
      }).join("");
    }

    function row(label, value) {
      return '<dt>' + label + '</dt><dd><button type="button" class="copy-btn" ' +
        'data-copy="' + value + '" style="all:unset;cursor:pointer">' +
        value + '</button></dd>';
    }

    function update() {
      var hex = input.value;
      var rgb = hexToRgb(hex);
      var hsl = rgbToHsl(rgb[0], rgb[1], rgb[2]);
      var hsv = rgbToHsv(rgb[0], rgb[1], rgb[2]);
      var cmyk = rgbToCmyk(rgb[0], rgb[1], rgb[2]);
      preview.style.background = hex;
      rows.innerHTML =
        row("HEX", hex.toUpperCase()) +
        row("RGB", "rgb(" + rgb.join(", ") + ")") +
        row("HSL", "hsl(" + hsl[0] + ", " + hsl[1] + "%, " + hsl[2] + "%)") +
        row("HSV", "hsv(" + hsv[0] + ", " + hsv[1] + "%, " + hsv[2] + "%)") +
        row("CMYK", "cmyk(" + cmyk.join("%, ") + "%)");
      var ramp = "";
      for (var i = 0; i < 9; i++) {
        var light = 94 - (i / 8) * 86;
        var shade = hslToHex(hsl[0], hsl[1], light);
        ramp += '<button type="button" class="color-shade copy-btn" ' +
          'style="background:' + shade + '" data-copy="' + shade + '" ' +
          'title="' + shade + '"></button>';
      }
      shades.innerHTML = ramp;
    }

    input.addEventListener("input", update);
    update();
  });

  /* ==================================================== periodic table ==== */

  $$('[data-widget="periodic-table"]').forEach(function (root) {
    var search = $("[data-pt-search]", root);
    var detail = $("[data-pt-detail]", root);
    var cells = $$(".pt-cell[data-el]", root);

    var CATEGORY = {
      alkali: "Alkali metal", alkaline: "Alkaline earth metal",
      transition: "Transition metal", "post-transition": "Post-transition metal",
      metalloid: "Metalloid", nonmetal: "Reactive nonmetal", halogen: "Halogen",
      noble: "Noble gas", lanthanide: "Lanthanide", actinide: "Actinide",
      unknown: "Unknown properties"
    };

    function showDetail(cell) {
      var parts = cell.getAttribute("data-el").split("|");
      var num = parts[0], sym = parts[1], name = parts[2], mass = parts[3],
          cat = parts[4], group = parts[5], period = parts[6];
      var swatch = getComputedStyle(cell).backgroundColor;
      detail.innerHTML =
        '<div class="pt-detail-head">' +
          '<span class="pt-detail-sym" style="background:' + swatch + '">' + sym + '</span>' +
          '<div><h3 style="margin:0">' + name + '</h3>' +
          '<span style="color:var(--text-3)">' + (CATEGORY[cat] || cat) + '</span></div>' +
        '</div>' +
        '<dl class="pt-detail-rows">' +
          '<dt>Atomic number</dt><dd>' + num + '</dd>' +
          '<dt>Symbol</dt><dd>' + sym + '</dd>' +
          '<dt>Atomic mass</dt><dd>' + mass + ' u</dd>' +
          '<dt>Group</dt><dd>' + (group === "0" ? "—" : group) + '</dd>' +
          '<dt>Period</dt><dd>' + period + '</dd>' +
          '<dt>Category</dt><dd>' + (CATEGORY[cat] || cat) + '</dd>' +
        '</dl>' +
        '<div class="answer-actions"><a class="btn btn-ghost btn-sm" href="/search?q=' +
          encodeURIComponent(name + " element") + '">Search ' + name + '</a></div>';
      detail.hidden = false;
      detail.scrollIntoView({ block: "nearest", behavior: reduceMotion ? "auto" : "smooth" });
    }

    cells.forEach(function (cell) {
      cell.addEventListener("click", function () { showDetail(cell); });
    });

    if (search) {
      search.addEventListener("input", function () {
        var q = search.value.trim().toLowerCase();
        cells.forEach(function (cell) {
          var match = !q ||
            cell.getAttribute("data-name").indexOf(q) === 0 ||
            cell.getAttribute("data-symbol") === q ||
            cell.getAttribute("data-name").indexOf(q) >= 0;
          cell.classList.toggle("dim", !match);
        });
      });
    }
  });
})();
