/* console.js —— 無依賴。三件事：表單非同步套用、設定頁的搜尋 / 只看已修改 / 還原 / 未套用列、switch 文字。 */
(function () {
  function swapTemplates(root) {
    root.querySelectorAll('template[data-swap]').forEach(function (t) {
      var target = document.querySelector(t.getAttribute('data-swap'));
      if (target) target.innerHTML = t.innerHTML;
      t.remove();
    });
  }

  /* ---- 非同步套用：POST 後用回來的 HTML 取代該 group ---- */
  function submitAsync(form) {
    var btn = form.querySelector('.group-hd button:not([type=button])') || form.querySelector('button.primary');
    if (btn) { btn.disabled = true; btn.textContent = '套用中…'; }
    return fetch(form.action, { method: 'POST', body: new FormData(form), credentials: 'same-origin' })
      .then(function (r) { if (r.redirected) { location.href = r.url; return ''; } return r.text(); })
      .then(function (html) {
        if (!html) return;
        var box = document.createElement('div');
        box.innerHTML = html;
        swapTemplates(box);
        var fresh = box.querySelector('section.group') || box.firstElementChild;
        var old = form.closest('section.group');
        old.replaceWith(fresh);
        bind(fresh);
        applyFilters();
        updateDirty();
      })
      .catch(function (e) { if (btn) { btn.disabled = false; btn.textContent = '套用'; } console.error(e); });
  }

  /* ---- 未套用偵測 ---- */
  function currentValue(el) {
    if (el.type === 'checkbox') return el.checked ? 'on' : '';
    return el.value;
  }
  function dirtyGroups() {
    var out = [];
    document.querySelectorAll('section.group').forEach(function (g) {
      var dirty = false;
      g.querySelectorAll('[data-initial]').forEach(function (el) {
        if (currentValue(el) !== el.getAttribute('data-initial')) dirty = true;
      });
      g.classList.toggle('dirty', dirty);
      if (dirty) out.push(g);
    });
    return out;
  }
  function updateDirty() {
    var bar = document.getElementById('dirty-bar');
    if (!bar) return;
    var d = dirtyGroups();
    bar.hidden = d.length === 0;
    var t = document.getElementById('dirty-text');
    if (t && d.length) t.textContent = d.length + ' 個區段有未套用的變更：' + d.map(function (g) { return g.getAttribute('data-group'); }).join('、');
  }

  /* ---- 搜尋 / 只看已修改 ---- */
  function applyFilters() {
    var q = (document.getElementById('settings-search') || {}).value || '';
    q = q.trim().toLowerCase();
    var onlyMod = (document.getElementById('only-modified') || {}).checked;
    document.querySelectorAll('section.task').forEach(function (task) {
      var taskVisible = false;
      task.querySelectorAll('section.group').forEach(function (g) {
        var any = false;
        g.querySelectorAll('.field-row').forEach(function (row) {
          var hit = (!q || (row.getAttribute('data-search') || '').indexOf(q) >= 0)
                 && (!onlyMod || row.classList.contains('modified') || row.classList.contains('dirty'));
          row.hidden = !hit;
          if (hit) any = true;
        });
        g.hidden = !any;
        if (any) taskVisible = true;
      });
      if (task.id !== 'changes') task.hidden = !taskVisible;
    });
  }

  function bind(root) {
    root.querySelectorAll('form[data-async]').forEach(function (form) {
      form.addEventListener('submit', function (ev) { ev.preventDefault(); submitAsync(form); });
    });
    root.querySelectorAll('label.switch input').forEach(function (cb) {
      cb.addEventListener('change', function () {
        var v = cb.parentElement.querySelector('.val');
        if (v) v.textContent = cb.checked ? 'true' : 'false';
      });
    });
    root.querySelectorAll('[data-initial]').forEach(function (el) {
      el.addEventListener('input', updateDirty);
      el.addEventListener('change', updateDirty);
    });
    root.querySelectorAll('button.reset').forEach(function (b) {
      b.addEventListener('click', function () {
        var row = b.closest('.field-row');
        var el = row.querySelector('[data-initial]');
        if (!el) return;
        var def = b.getAttribute('data-default') || '';
        if (el.type === 'checkbox') { el.checked = def === 'true'; el.dispatchEvent(new Event('change')); }
        else { el.value = ''; el.dispatchEvent(new Event('input')); }
      });
    });
  }

  bind(document);
  var s = document.getElementById('settings-search');
  if (s) s.addEventListener('input', applyFilters);
  var m = document.getElementById('only-modified');
  if (m) m.addEventListener('change', applyFilters);
  var apply = document.getElementById('dirty-apply');
  if (apply) apply.addEventListener('click', function () {
    var forms = dirtyGroups().map(function (g) { return g.querySelector('form[data-async]'); });
    (function next() { var f = forms.shift(); if (f) submitAsync(f).then(next); })();
  });
  var discard = document.getElementById('dirty-discard');
  if (discard) discard.addEventListener('click', function () { location.reload(); });
})();
