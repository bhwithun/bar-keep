(function () {
  var host = document.querySelector("[data-quote-ticker]");
  var track = host && host.querySelector("[data-quote-track]");
  var dataId = host && host.getAttribute("data-quote-source");
  var dataEl = dataId ? document.getElementById(dataId) : document.getElementById("quote-ticker-data");
  if (!host || !track || !dataEl) return;

  var pool = [];
  try {
    pool = JSON.parse(dataEl.textContent || "[]");
  } catch (e) {
    pool = [];
  }
  if (!pool.length) {
    host.hidden = true;
    return;
  }

  var index = 0;
  var offset = 0;
  var lastTs = 0;
  var pxPerSec = 70;
  var itemClass = host.getAttribute("data-item-class") || "tv-ticker-item";

  function shuffle(arr) {
    for (var i = arr.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = arr[i];
      arr[i] = arr[j];
      arr[j] = t;
    }
  }

  function nextQuote() {
    if (index >= pool.length) {
      var prev = pool.length ? pool[pool.length - 1] : null;
      shuffle(pool);
      if (pool.length > 1 && pool[0] === prev) {
        var t = pool[0];
        pool[0] = pool[1];
        pool[1] = t;
      }
      index = 0;
    }
    return pool[index++];
  }

  function fillItem(el, q) {
    el.textContent = "";
    el.appendChild(document.createTextNode("“" + q.quote + "” "));
    var em = document.createElement("em");
    em.textContent = "— " + (q.movie || "");
    el.appendChild(em);
  }

  function makeItem(q) {
    var el = document.createElement("span");
    el.className = itemClass;
    fillItem(el, q);
    return el;
  }

  function fillTrack() {
    var need = host.clientWidth + 480;
    var guard = 0;
    while (track.scrollWidth < need && guard < 40) {
      track.appendChild(makeItem(nextQuote()));
      guard += 1;
    }
  }

  function recycle() {
    while (track.firstElementChild) {
      var first = track.firstElementChild;
      var w = first.offsetWidth;
      if (w < 1 || offset + w > 0) break;
      offset += w;
      fillItem(first, nextQuote());
      track.appendChild(first);
    }
    track.style.transform = "translateX(" + offset + "px)";
  }

  function matchLegacySpeed() {
    var items = track.children;
    if (!items.length) return;
    var total = 0;
    for (var i = 0; i < items.length; i++) total += items[i].offsetWidth;
    var avg = total / items.length;
    if (avg > 1) pxPerSec = (avg * 12) / 90;
  }

  shuffle(pool);
  fillTrack();
  matchLegacySpeed();

  function frame(ts) {
    if (!lastTs) lastTs = ts;
    var dt = Math.min(48, ts - lastTs) / 1000;
    lastTs = ts;
    if (!document.hidden) {
      offset -= pxPerSec * dt;
      recycle();
    }
    requestAnimationFrame(frame);
  }

  document.addEventListener("visibilitychange", function () {
    lastTs = 0;
  });
  window.addEventListener("resize", function () {
    fillTrack();
    matchLegacySpeed();
  });
  requestAnimationFrame(frame);
})();
