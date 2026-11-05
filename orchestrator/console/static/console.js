/* console.js —— 整個控制台唯一的 JS：表單非同步送出、局部換頁。2 KB，無依賴。
   data-async 的表單：POST 後用回來的 HTML 取代表單所在的 section，
   回應裡的 <template data-swap="#id"> 會把對應元素的內容也換掉（改動紀錄、警示條）。 */
(function () {
  function swapTemplates(root) {
    root.querySelectorAll('template[data-swap]').forEach(function (t) {
      var target = document.querySelector(t.getAttribute('data-swap'));
      if (target) target.innerHTML = t.innerHTML;
      t.remove();
    });
  }
  function bind(root) {
    root.querySelectorAll('form[data-async]').forEach(function (form) {
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        var btn = form.querySelector('button.primary');
        if (btn) { btn.disabled = true; btn.textContent = '套用中…'; }
        fetch(form.action, { method: 'POST', body: new FormData(form), credentials: 'same-origin' })
          .then(function (r) { if (r.redirected) { location.href = r.url; return ''; } return r.text(); })
          .then(function (html) {
            if (!html) return;
            var box = document.createElement('div');
            box.innerHTML = html;
            swapTemplates(box);
            var fresh = box.firstElementChild;
            var old = form.closest('section');
            old.replaceWith(fresh);
            bind(fresh);
            fresh.scrollIntoView({ block: 'nearest' });
          })
          .catch(function (e) { alert('套用失敗：' + e); if (btn) { btn.disabled = false; btn.textContent = 'Apply'; } });
      });
    });
    root.querySelectorAll('label.switch input').forEach(function (cb) {
      cb.addEventListener('change', function () { cb.nextElementSibling.textContent = cb.checked ? 'true' : 'false'; });
    });
  }
  bind(document);
})();
