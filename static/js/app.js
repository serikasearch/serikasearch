/* SerikaSearch — front-end behaviour: clear button, image lightbox,
   keyboard navigation, and gentle progressive enhancement. No dependencies. */

(function () {
  "use strict";

  /* ----------------------------------------------------------- searchbox --
     Show/hide the clear (×) button based on input, and wire it to clear + focus. */
  function initSearchbox(box) {
    var input = box.querySelector('input[name="q"]');
    var clear = box.querySelector(".clear-btn");
    if (!input || !clear) return;

    function sync() {
      box.classList.toggle("has-value", input.value.length > 0);
    }
    sync();
    input.addEventListener("input", sync);
    input.addEventListener("change", sync);

    clear.addEventListener("click", function (e) {
      e.preventDefault();
      input.value = "";
      sync();
      input.focus();
      // if we're on a results page, return home on clear
      if (window.location.pathname === "/search" && input.form) {
        window.location.href = "/";
      }
    });

    // "/" focuses search from anywhere, unless already typing in a field.
    document.addEventListener("keydown", function (e) {
      if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
        e.preventDefault();
        input.focus();
        input.select();
      }
    });
  }

  document.querySelectorAll(".searchbox").forEach(initSearchbox);

  /* --------------------------------------------- justified image grid (cont.) --
     The crawler only learns dimensions for images that declare them, so most
     cards start at a neutral 3:2. Once each thumbnail actually loads, read its
     real natural size and update the card's --ratio so the justified rows pack
     images at their true, varied proportions (Google Images style). */
  function setTrueRatio(img) {
    var card = img.closest(".image-card");
    if (!card) return;
    var w = img.naturalWidth, h = img.naturalHeight;
    if (!w || !h) return;
    var ratio = (w / h);
    // guard against absurd ratios (panoramas / thin strips) so rows stay sane
    ratio = Math.max(0.4, Math.min(ratio, 3.0));
    card.style.setProperty("--ratio", ratio.toFixed(4));
  }

  document.querySelectorAll(".image-card .thumb").forEach(function (img) {
    if (img.complete && img.naturalWidth) setTrueRatio(img);
    else img.addEventListener("load", function () { setTrueRatio(img); });
  });

  /* -------------------------------------------------------------- lightbox --
     Clicking an image card opens a sidebar on the right that pushes the grid
     aside (Google Images style). The sidebar shows the image, title, host,
     visit/open actions, and a grid of similar images. Esc / close button
     dismisses it; arrow keys move between cards; clicking a similar image
     switches to it with a crossfade. */
  var lightbox = document.getElementById("lightbox");
  if (lightbox) {
    var lbImg = lightbox.querySelector(".lb-img");
    var lbTitle = lightbox.querySelector(".lb-title");
    var lbHost = lightbox.querySelector(".lb-host");
    var lbFavicon = lightbox.querySelector(".lb-favicon");
    var lbVisit = lightbox.querySelector(".lb-visit");
    var lbOpen = lightbox.querySelector(".lb-open");
    var similarGrid = lightbox.querySelector(".lb-similar-grid");
    var similarEmpty = lightbox.querySelector(".lb-similar-empty");
    var cards = Array.prototype.slice.call(
      document.querySelectorAll(".image-card")
    );
    var current = -1;

    function setImage(src, title, page, host) {
      lbImg.classList.add("switching");
      function swap() {
        lbImg.src = src;
        lbImg.alt = title;
        lbTitle.textContent = title;
        lbHost.textContent = host;
        lbHost.href = page || src;
        lbVisit.href = page || src;
        lbOpen.href = src;
        if (lbFavicon) lbFavicon.src = "/icon?h=" + encodeURIComponent(host);
        if (lbImg.complete && lbImg.naturalWidth) {
          lbImg.classList.remove("switching");
        } else {
          lbImg.onload = function () { lbImg.classList.remove("switching"); };
        }
      }
      if (lbImg.src) window.setTimeout(swap, 120);
      else swap();
    }

    function loadSimilar(src, page, host) {
      similarGrid.innerHTML = "";
      similarEmpty.hidden = true;
      var url = "/api/similar?src=" + encodeURIComponent(src) +
                "&page=" + encodeURIComponent(page) +
                "&host=" + encodeURIComponent(host);
      fetch(url).then(function (r) { return r.json(); }).then(function (items) {
        if (!items || !items.length) { similarEmpty.hidden = false; return; }
        items.forEach(function (it, idx) {
          var a = document.createElement("a");
          a.className = "lb-similar-item";
          a.style.setProperty("--si", idx);
          a.style.setProperty("--ratio", it.ratio || 1.5);
          a.href = it.page || it.src;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.title = it.title || it.host || "";
          var img = document.createElement("img");
          img.src = it.src;
          img.alt = it.title || "";
          img.loading = "lazy";
          img.referrerPolicy = "no-referrer";
          img.decoding = "async";
          a.appendChild(img);
          a.addEventListener("click", function (e) {
            if (e.metaKey || e.ctrlKey || e.button === 1) return;
            e.preventDefault();
            setImage(it.src, it.title, it.page, it.host);
            loadSimilar(it.src, it.page, it.host);
          });
          similarGrid.appendChild(a);
        });
      }).catch(function () { similarEmpty.hidden = false; });
    }

    function open(i) {
      var card = cards[i];
      if (!card) return;
      current = i;
      var src = card.getAttribute("data-src");
      var page = card.getAttribute("data-page");
      var title = card.getAttribute("data-title") || "";
      var host = card.getAttribute("data-host") || "";

      setImage(src, title, page, host);
      loadSimilar(src, page, host);

      lightbox.hidden = false;
      void lightbox.offsetWidth;
      lightbox.classList.add("open");
      lightbox.setAttribute("aria-hidden", "false");
      document.body.classList.add("lb-open");
    }

    function close() {
      lightbox.classList.remove("open");
      lightbox.setAttribute("aria-hidden", "true");
      document.body.classList.remove("lb-open");
      window.setTimeout(function () {
        if (!lightbox.classList.contains("open")) {
          lightbox.hidden = true;
          lbImg.src = "";
          similarGrid.innerHTML = "";
        }
      }, 320);
    }

    function move(delta) {
      if (current < 0) return;
      var n = current + delta;
      while (n >= 0 && n < cards.length && cards[n].classList.contains("img-broken")) {
        n += delta;
      }
      if (n >= 0 && n < cards.length) open(n);
    }

    cards.forEach(function (card, i) {
      card.addEventListener("click", function (e) {
        if (e.metaKey || e.ctrlKey || e.button === 1) return;
        e.preventDefault();
        open(i);
      });
    });

    lightbox.addEventListener("click", function (e) {
      if (e.target.hasAttribute("data-close") || e.target.closest("[data-close]")) close();
    });

    document.addEventListener("keydown", function (e) {
      if (lightbox.hidden || !lightbox.classList.contains("open")) return;
      if (e.key === "Escape") { e.preventDefault(); close(); }
      else if (e.key === "ArrowRight") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); move(-1); }
    });

    function preload(i) {
      var c = cards[i];
      if (!c) return;
      var im = new Image();
      im.src = c.getAttribute("data-src");
    }
    var origOpen = open;
    open = function (i) {
      origOpen(i);
      preload(i + 1);
      preload(i - 1);
    };
  }

  /* ------------------------------------------------------------- video modal --
     Clicking a video card's thumbnail opens an embedded player in a modal
     overlay. Esc / scrim / close button dismisses it. */
  var videoModal = document.getElementById("videoModal");
  if (videoModal) {
    var vmFrame = document.getElementById("videoModalFrame");

    function openVideoModal(embedUrl, pageUrl) {
      if (!embedUrl) return;
      // Mute autoplay so Firefox allows it; user can unmute in the player.
      var sep = embedUrl.indexOf("?") >= 0 ? "&" : "?";
      vmFrame.src = embedUrl + sep + "autoplay=1&mute=1";
      // Keep a direct link as fallback in case the embed is blocked.
      var fallback = videoModal.querySelector(".video-modal-fallback");
      if (fallback && pageUrl) fallback.href = pageUrl;
      videoModal.hidden = false;
      void videoModal.offsetWidth;
      videoModal.classList.add("open");
      videoModal.setAttribute("aria-hidden", "false");
      document.body.style.overflow = "hidden";
    }

    function closeVideoModal() {
      videoModal.classList.remove("open");
      videoModal.setAttribute("aria-hidden", "true");
      document.body.style.overflow = "";
      setTimeout(function () {
        if (!videoModal.classList.contains("open")) {
          videoModal.hidden = true;
          vmFrame.src = "";
        }
      }, 280);
    }

    // Open modal when clicking a video thumbnail.
    document.querySelectorAll("[data-embed-trigger]").forEach(function (el) {
      el.addEventListener("click", function (e) {
        if (e.metaKey || e.ctrlKey || e.button === 1) return;
        var card = el.closest("[data-embed]");
        if (!card) return;
        var embedUrl = card.getAttribute("data-embed");
        if (!embedUrl) return;
        e.preventDefault();
        openVideoModal(embedUrl, card.getAttribute("data-page"));
      });
    });

    // Close on scrim / close button.
    videoModal.addEventListener("click", function (e) {
      if (e.target.hasAttribute("data-vm-close") ||
          e.target.closest("[data-vm-close]")) {
        closeVideoModal();
      }
    });

    // Esc to close.
    document.addEventListener("keydown", function (e) {
      if (videoModal.hidden || !videoModal.classList.contains("open")) return;
      if (e.key === "Escape") { e.preventDefault(); closeVideoModal(); }
    });
  }

  /* ----------------------------------------------------- progressive reveal --
     Stagger result cards in even when JS added them late (it doesn't here, but
     this keeps the entrance smooth if the DOM grows). */
  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) {
          en.target.style.animationPlayState = "running";
          io.unobserve(en.target);
        }
      });
    }, { rootMargin: "200px" });
    document.querySelectorAll(".result, .image-card").forEach(function (el) {
      // only pause if the page actually uses the staggered delay
      if (el.style.animationDelay || getComputedStyle(el).animationDelay !== "0s") {
        io.observe(el);
      }
    });
  }
})();
