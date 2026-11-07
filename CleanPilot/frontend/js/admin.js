/* CleanPilot 管理后台逻辑 */
(function () {
  'use strict';

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var esc = CP.escapeHtml;
  var fmt = CP.fmtBytes;

  var VIEW_META = {
    overview: { title: '运行概览', sub: '平台整体运行状态与治理成效' },
    devices: { title: '设备管理', sub: '管理接入设备、保护路径与清理策略' },
    audit: { title: '审计日志', sub: '记录所有关键操作，支持追溯' }
  };

  var devices = [];

  function boot() {
    bindEvents();
    if (!CP.token.get()) {
      showGuard();
      return;
    }
    CP.me().then(function (data) {
      if (data.user.role !== 'admin') {
        showGuard();
        return;
      }
      $('#adminName').textContent = data.user.username;
      $('#adminAvatar').textContent = (data.user.username || 'A').charAt(0).toUpperCase();
      $('#adminApp').classList.remove('hidden');
      loadSummary();
      loadDevices();
      loadAudits();
    }).catch(function () {
      showGuard();
    });
  }

  function showGuard() {
    $('#guard').classList.remove('hidden');
    $('#adminApp').classList.add('hidden');
  }

  function bindEvents() {
    $('#adminNav').addEventListener('click', function (e) {
      var btn = e.target.closest('[data-admin-view]');
      if (!btn) return;
      switchView(btn.getAttribute('data-admin-view'));
    });
    $('#adminRefresh').onclick = function () {
      refreshCurrent();
      toast('已刷新');
    };
    $('#adminAddDevice').onclick = openAddDeviceModal;
    $('#adminLogout').onclick = function () {
      CP.logout().catch(function () { }).then(function () {
        CP.token.clear();
        location.href = '/index.html';
      });
    };
    $('#adminDeviceTable').addEventListener('click', function (e) {
      var btn = e.target.closest('[data-device-act]');
      if (!btn) return;
      var id = Number(btn.getAttribute('data-id'));
      var device = devices.filter(function (d) { return d.id === id; })[0];
      if (!device) return;
      if (btn.getAttribute('data-device-act') === 'policy') openPolicyModal(device);
      if (btn.getAttribute('data-device-act') === 'remove') {
        confirmModal('移除设备', '确认从平台移除「' + device.name + '」？仅删除记录，不会删除磁盘文件。', function () {
          CP.removeDevice(id).then(function () {
            toast('设备已移除');
            loadDevices();
            loadSummary();
          }).catch(function (err) { toast(err.message, 'error'); });
        });
      }
    });
    $('#modalRoot').addEventListener('click', function (e) {
      if (e.target.closest('[data-close]') || e.target.classList.contains('modal-overlay')) closeModal();
    });
  }

  function switchView(view) {
    $$('.admin-view').forEach(function (v) { v.classList.add('hidden'); });
    $('#view-admin-' + view).classList.remove('hidden');
    $$('.nav-item').forEach(function (n) {
      n.classList.toggle('active', n.getAttribute('data-admin-view') === view);
    });
    var meta = VIEW_META[view] || VIEW_META.overview;
    $('#adminTitle').textContent = meta.title;
    $('#adminSub').textContent = meta.sub;
  }

  function refreshCurrent() {
    loadSummary();
    loadDevices();
    loadAudits();
  }

  /* ---------------- 概览 ---------------- */
  function loadSummary() {
    CP.adminSummary().then(function (data) {
      $('#asDevices').textContent = data.devices;
      $('#asTasks').textContent = data.tasks;
      $('#asReleased').textContent = fmt(data.released);
      $('#asScans').textContent = data.scans;
      $('#asRecycle').textContent = data.recycle;
      var mem = data.system;
      if (mem && mem.total) {
        $('#asMem').textContent = mem.percent + '%';
        $('#asMemSub').textContent = fmt(mem.used) + ' / ' + fmt(mem.total);
        $('#memPct').textContent = mem.percent + '%';
        $('#memBar').style.width = mem.percent + '%';
        $('#memDetail').textContent = '已用 ' + fmt(mem.used) + '，可用 ' + fmt(mem.total - mem.used) + '，共 ' + fmt(mem.total);
      } else {
        $('#asMem').textContent = '—';
        $('#asMemSub').textContent = '暂无法读取';
        $('#memPct').textContent = '—';
        $('#memDetail').textContent = '当前系统不支持内存信息读取';
      }
    }).catch(function (err) { toast(err.message, 'error'); });
  }

  /* ---------------- 设备 ---------------- */
  function loadDevices() {
    CP.adminDevices().then(function (data) {
      devices = data.devices || [];
      var box = $('#adminDeviceTable');
      if (!devices.length) {
        box.innerHTML = '<div class="table-empty">暂无接入设备</div>';
        return;
      }
      box.innerHTML = '<table class="data-table"><thead><tr>' +
        '<th>设备 / 路径</th><th>所有者</th><th>磁盘使用</th><th>回收站</th><th>任务</th><th>策略</th><th>操作</th>' +
        '</tr></thead><tbody>' + devices.map(function (d) {
          var total = d.total || 0;
          var used = Math.max(0, total - (d.free || 0));
          var pct = total ? Math.round(used * 100 / total) : 0;
          return '<tr>' +
            '<td><div class="device-name">' + esc(d.name) + '</div>' +
            '<div class="device-path">' + esc(d.root_path) + '</div>' +
            (d.exists === false ? '<span class="badge risk-R3">路径不可用</span>' : '') + '</td>' +
            '<td class="device-cell-owner">' + esc(d.owner || '—') + '</td>' +
            '<td><div class="mini-storage"><div class="meta"><span>' + fmt(used) + '</span><span>' + pct + '%</span></div>' +
            '<div class="bar"><i style="width:' + pct + '%"></i></div></div></td>' +
            '<td>' + fmt(d.recycle_bytes) + '</td>' +
            '<td>' + (d.task_count || 0) + ' 次</td>' +
            '<td><span class="badge risk-R1">保留 ' + d.retention_days + ' 天</span> ' +
            '<span class="badge risk-R2">上限 ' + d.max_risk + '</span>' +
            '<div class="device-path">' + (d.protected_paths && d.protected_paths.length ? (d.protected_paths.length + ' 条保护路径') : '无保护路径') + '</div></td>' +
            '<td><div class="row-actions">' +
            '<button class="btn ghost small" data-device-act="policy" data-id="' + d.id + '">策略</button>' +
            '<button class="btn ghost small danger" data-device-act="remove" data-id="' + d.id + '">移除</button>' +
            '</div></td></tr>';
        }).join('') + '</tbody></table>';
    }).catch(function (err) { toast(err.message, 'error'); });
  }

  /* ---------------- 审计 ---------------- */
  function loadAudits() {
    CP.adminAudit().then(function (data) {
      var rows = data.audits || [];
      var box = $('#adminAuditTable');
      if (!rows.length) {
        box.innerHTML = '<div class="table-empty">暂无操作记录</div>';
        return;
      }
      box.innerHTML = '<table class="data-table"><thead><tr>' +
        '<th>时间</th><th>操作者</th><th>动作</th><th>设备</th><th>范围 / 详情</th><th>结果</th>' +
        '</tr></thead><tbody>' + rows.map(function (a) {
          var stateClass = a.result === 'ok' || a.result === 'completed' ? 'state-completed'
            : (a.result === 'failed' ? 'state-failed' : 'state-partial');
          return '<tr>' +
            '<td class="cell-size">' + CP.fmtTime(a.ts) + '</td>' +
            '<td>' + esc(a.actor || '—') + '</td>' +
            '<td><b>' + esc(a.action) + '</b></td>' +
            '<td>' + (a.device_id === null || a.device_id === undefined ? '—' : '#' + a.device_id) + '</td>' +
            '<td class="cell-reason">' + esc(a.scope || '') + '</td>' +
            '<td><span class="state-tag ' + stateClass + '">' + esc(a.result || '—') + '</span></td>' +
            '</tr>';
        }).join('') + '</tbody></table>';
    }).catch(function (err) { toast(err.message, 'error'); });
  }

  /* ---------------- 弹窗 ---------------- */
  function openAddDeviceModal() {
    $('#modalRoot').innerHTML = '<div class="modal-overlay"><div class="modal">' +
      '<div class="modal-head"><h3>添加设备</h3><button class="icon-btn" data-close>×</button></div>' +
      '<div class="modal-body"><form class="modal-form" id="adminAddForm">' +
      '<label>设备名称<input id="adminDevName" placeholder="例如：办公室台式机" required></label>' +
      '<label>存储路径<input id="adminDevPath" placeholder="例如：D:\\资料库" required>' +
      '<span class="hint">路径必须是服务器上真实存在的文件夹。</span></label>' +
      '<div class="modal-foot" style="padding:4px 0 0">' +
      '<button type="button" class="btn ghost" data-close>取消</button>' +
      '<button type="submit" class="btn primary">添加</button></div>' +
      '</form></div></div></div>';
    $('#adminAddForm').onsubmit = function (e) {
      e.preventDefault();
      var submit = $('button[type=submit]', e.target);
      submit.disabled = true;
      CP.addDevice($('#adminDevName').value.trim(), $('#adminDevPath').value.trim()).then(function () {
        closeModal();
        toast('设备已添加', 'success');
        loadDevices();
        loadSummary();
      }).catch(function (err) {
        submit.disabled = false;
        toast(err.message, 'error');
      });
    };
  }

  function openPolicyModal(d) {
    var paths = (d.protected_paths || []).join('\n');
    $('#modalRoot').innerHTML = '<div class="modal-overlay"><div class="modal">' +
      '<div class="modal-head"><h3>治理策略 · ' + esc(d.name) + '</h3><button class="icon-btn" data-close>×</button></div>' +
      '<div class="modal-body"><form class="modal-form" id="policyForm">' +
      '<label>回收站保留天数（7 - 90）<input id="policyDays" type="number" min="7" max="90" value="' + d.retention_days + '"></label>' +
      '<label>允许清理的最高风险等级<select id="policyRisk">' +
      '<option value="R1"' + (d.max_risk === 'R1' ? ' selected' : '') + '>R1 · 仅安全/建议清理项</option>' +
      '<option value="R2"' + (d.max_risk === 'R2' ? ' selected' : '') + '>R2 · 允许需谨慎项（需二次确认）</option>' +
      '</select><span class="hint">R3 受保护内容永远不会被清理。</span></label>' +
      '<label>保护路径（每行一条，相对设备根目录或绝对路径）' +
      '<textarea id="policyPaths" placeholder="例如：DCIM\nDocuments/合同">' + esc(paths) + '</textarea></label>' +
      '<div class="modal-foot" style="padding:4px 0 0">' +
      '<button type="button" class="btn ghost" data-close>取消</button>' +
      '<button type="submit" class="btn primary">保存策略</button></div>' +
      '</form></div></div></div>';
    $('#policyForm').onsubmit = function (e) {
      e.preventDefault();
      var submit = $('button[type=submit]', e.target);
      submit.disabled = true;
      var lines = $('#policyPaths').value.split('\n').map(function (s) { return s.trim(); }).filter(Boolean);
      CP.savePolicy(d.id, {
        protected_paths: lines,
        retention_days: Number($('#policyDays').value) || d.retention_days,
        max_risk: $('#policyRisk').value
      }).then(function () {
        closeModal();
        toast('策略已更新', 'success');
        loadDevices();
      }).catch(function (err) {
        submit.disabled = false;
        toast(err.message, 'error');
      });
    };
  }

  function confirmModal(title, text, onOk) {
    $('#modalRoot').innerHTML = '<div class="modal-overlay"><div class="modal" style="max-width:420px">' +
      '<div class="modal-head"><h3>' + esc(title) + '</h3><button class="icon-btn" data-close>×</button></div>' +
      '<div class="modal-body"><p class="muted">' + esc(text) + '</p></div>' +
      '<div class="modal-foot"><button class="btn ghost" data-close>取消</button>' +
      '<button class="btn primary" id="confirmOk">确认</button></div></div></div>';
    $('#confirmOk').onclick = function () {
      closeModal();
      onOk();
    };
  }

  function closeModal() { $('#modalRoot').innerHTML = ''; }

  function toast(message, type) {
    var el = document.createElement('div');
    el.className = 'toast' + (type ? ' ' + type : '');
    el.textContent = message;
    $('#toastRoot').appendChild(el);
    setTimeout(function () {
      el.style.opacity = '0';
      el.style.transition = 'all .25s';
      setTimeout(function () { el.remove(); }, 260);
    }, 2600);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
