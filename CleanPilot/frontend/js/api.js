/* CleanPilot 前端 API 封装（原生 fetch，无第三方依赖） */
(function () {
  'use strict';

  var TOKEN_KEY = 'cp_token';
  var DEVICE_KEY = 'cp_device';

  function token() { return localStorage.getItem(TOKEN_KEY) || ''; }
  function setToken(t) {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  }

  async function request(path, options) {
    options = options || {};
    var opts = { method: options.method || 'GET', headers: {} };
    if (options.body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(options.body);
    }
    var t = token();
    if (t) opts.headers['Authorization'] = 'Bearer ' + t;
    var res = await fetch(path, opts);
    var text = await res.text();
    var data = {};
    try { data = text ? JSON.parse(text) : {}; } catch (e) { data = { error: text }; }
    if (!res.ok) {
      var err = new Error(data.error || ('请求失败 ' + res.status));
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function fmtBytes(n) {
    n = Number(n) || 0;
    if (n >= 1024 * 1024 * 1024) return (n / 1024 / 1024 / 1024).toFixed(1) + ' GB';
    if (n >= 1024 * 1024) return Math.round(n / 1024 / 1024) + ' MB';
    if (n >= 1024) return Math.round(n / 1024) + ' KB';
    return Math.round(n) + ' B';
  }

  function fmtTime(ts) {
    if (!ts) return '—';
    var d = new Date(ts * 1000);
    var p = function (x) { return x < 10 ? '0' + x : '' + x; };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }

  function escapeHtml(s) {
    return String(s === undefined || s === null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  window.CP = {
    token: {
      get: token,
      set: setToken,
      clear: function () { setToken(''); }
    },
    deviceId: {
      get: function () { return Number(localStorage.getItem(DEVICE_KEY)) || 0; },
      set: function (id) { localStorage.setItem(DEVICE_KEY, String(id)); }
    },
    request: request,

    login: function (username, password) {
      return request('/api/login', { method: 'POST', body: { username: username, password: password } });
    },
    register: function (username, password) {
      return request('/api/register', { method: 'POST', body: { username: username, password: password } });
    },
    logout: function () { return request('/api/logout', { method: 'POST' }); },
    me: function () { return request('/api/me'); },

    devices: function () { return request('/api/devices'); },
    addDevice: function (name, rootPath) {
      return request('/api/devices', { method: 'POST', body: { name: name, root_path: rootPath } });
    },
    removeDevice: function (id) {
      return request('/api/devices/remove', { method: 'POST', body: { device_id: id } });
    },
    savePolicy: function (id, payload) {
      return request('/api/devices/' + id + '/policy', { method: 'POST', body: payload });
    },

    overview: function (id) { return request('/api/overview?device_id=' + id); },
    analysis: function (id) { return request('/api/ai/analyze?device_id=' + id); },

    startScan: function (id) { return request('/api/scan', { method: 'POST', body: { device_id: id } }); },
    scan: function (id) { return request('/api/scan/' + id); },
    scanItems: function (id) { return request('/api/scan/' + id + '/items'); },

    createPlan: function (scanId, itemIds) {
      return request('/api/plan', { method: 'POST', body: { scan_id: scanId, item_ids: itemIds } });
    },
    execute: function (planId, ack) {
      return request('/api/execute', { method: 'POST', body: { plan_id: planId, acknowledge_r2: !!ack } });
    },
    task: function (id) { return request('/api/task/' + id); },

    recycle: function (id) { return request('/api/recycle?device_id=' + id); },
    restore: function (id) { return request('/api/recycle/' + id + '/restore', { method: 'POST' }); },
    deleteRecycle: function (id) { return request('/api/recycle/' + id + '/delete', { method: 'POST' }); },
    emptyRecycle: function (id) {
      return request('/api/recycle/empty', { method: 'POST', body: { device_id: id } });
    },

    reports: function (id) { return request('/api/reports?device_id=' + id); },
    chat: function (id, message) {
      return request('/api/ai/chat', { method: 'POST', body: { device_id: id, message: message } });
    },

    adminSummary: function () { return request('/api/admin/summary'); },
    adminDevices: function () { return request('/api/admin/devices'); },
    adminAudit: function () { return request('/api/admin/audit'); },

    fmtBytes: fmtBytes,
    fmtTime: fmtTime,
    escapeHtml: escapeHtml,
    riskLabel: { R0: '安全清理', R1: '建议清理', R2: '需谨慎', R3: '受保护' },
    riskColor: { R0: '#10b981', R1: '#3b82f6', R2: '#f59e0b', R3: '#8b94a7' }
  };
})();
