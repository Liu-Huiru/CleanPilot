/* CleanPilot 主应用逻辑 */
(function () {
  'use strict';

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var esc = CP.escapeHtml;
  var fmt = CP.fmtBytes;

  var VIEW_META = {
    overview: { title: '总览', sub: '设备存储状态与 AI 清理建议' },
    scan: { title: '扫描与清理', sub: '分级识别风险，安全释放存储空间' },
    recycle: { title: '回收站', sub: '保留期内可随时恢复已清理的文件' },
    reports: { title: '清理报告', sub: '历史清理任务与释放明细' }
  };

  var CATEGORY_BY_FILTER = {
    cache: '缓存与临时文件',
    installer: '安装包',
    duplicate: '重复文件',
    screenshot: '截图',
    large: '大体积文件'
  };

  var S = {
    user: null,
    devices: [],
    deviceId: 0,
    overview: null,
    analysis: null,
    scan: null,
    items: [],
    selection: new Set(),
    riskFilter: 'all',
    catFilter: 'all',
    search: '',
    scanTimer: null,
    taskTimer: null,
    recycleItems: [],
    chatStarted: false,
    view: 'overview'
  };

  /* ---------------- 启动与登录 ---------------- */
  function boot() {
    bindStaticEvents();
    if (!CP.token.get()) {
      showLogin();
      return;
    }
    CP.me().then(function (data) {
      S.user = data.user;
      enterApp();
    }).catch(function () {
      CP.token.clear();
      showLogin();
    });
  }

  function showLogin() {
    $('#loginScreen').classList.remove('hidden');
    $('#app').classList.add('hidden');
    $('#aiFab').classList.add('hidden');
    $('#aiDrawer').classList.add('hidden');
  }

  function enterApp() {
    $('#loginScreen').classList.add('hidden');
    $('#app').classList.remove('hidden');
    $('#aiFab').classList.remove('hidden');
    $('#userName').textContent = S.user.username;
    $('#userRole').textContent = S.user.role === 'admin' ? '管理员' : '普通用户';
    $('#userAvatar').textContent = (S.user.username || 'C').charAt(0).toUpperCase();
    $('#adminLink').classList.toggle('hidden', S.user.role !== 'admin');
    $('#addDeviceBtn').classList.remove('hidden');
    loadDevices();
  }

  function bindStaticEvents() {
    // 登录 / 注册切换
    $('#tabLogin').onclick = function () { switchAuthTab(true); };
    $('#tabRegister').onclick = function () { switchAuthTab(false); };
    $$('.demo-fill, [data-demo]').forEach(function (btn) {
      btn.onclick = function () {
        var parts = btn.getAttribute('data-demo').split('|');
        $('#loginUser').value = parts[0];
        $('#loginPass').value = parts[1];
        switchAuthTab(true);
      };
    });

    $('#loginForm').onsubmit = function (e) {
      e.preventDefault();
      var u = $('#loginUser').value.trim();
      var p = $('#loginPass').value;
      $('#loginError').textContent = '';
      CP.login(u, p).then(function (data) {
        CP.token.set(data.token);
        S.user = data.user;
        enterApp();
      }).catch(function (err) {
        $('#loginError').textContent = err.message;
      });
    };

    $('#registerForm').onsubmit = function (e) {
      e.preventDefault();
      var u = $('#regUser').value.trim();
      var p = $('#regPass').value;
      $('#loginError').textContent = '';
      CP.register(u, p).then(function (data) {
        CP.token.set(data.token);
        S.user = data.user;
        enterApp();
      }).catch(function (err) {
        $('#loginError').textContent = err.message;
      });
    };

    $('#logoutBtn').onclick = function () {
      CP.logout().catch(function () { }).then(function () {
        CP.token.clear();
        location.reload();
      });
    };

    $('#nav').addEventListener('click', function (e) {
      var btn = e.target.closest('.nav-item');
      if (!btn) return;
      switchView(btn.getAttribute('data-view'));
    });

    $('#deviceSelect').onchange = function () {
      var id = Number(this.value) || 0;
      if (!id) return;
      S.deviceId = id;
      CP.deviceId.set(id);
      resetDeviceState();
      loadDevice();
    };

    $('#refreshBtn').onclick = function () {
      refreshCurrentView(true);
    };
    $('#addDeviceBtn').onclick = openAddDeviceModal;
    $('#emptyAddDevice').onclick = openAddDeviceModal;

    $('#quickScan').onclick = function () { switchView('scan'); startScan(); };
    $('#quickClean').onclick = function () { switchView('scan'); };
    $('#openAiBtn').onclick = openAiDrawer;

    // 扫描面板按钮（事件委托）
    $('#scanStatusCard').addEventListener('click', function (e) {
      var btn = e.target.closest('[data-scan-action]');
      if (!btn) return;
      startScan();
    });

    $('#riskChips').addEventListener('click', function (e) {
      var chip = e.target.closest('[data-risk]');
      if (!chip) return;
      S.riskFilter = chip.getAttribute('data-risk');
      renderChips();
      renderItemTable();
    });

    $('#categoryChips').addEventListener('click', function (e) {
      var chip = e.target.closest('[data-cat]');
      if (!chip) return;
      S.catFilter = chip.getAttribute('data-cat');
      renderChips();
      renderItemTable();
    });

    $('#itemSearch').oninput = function () {
      S.search = this.value.trim().toLowerCase();
      renderItemTable();
    };

    $('#btnSelectSafe').onclick = function () {
      S.items.forEach(function (it) {
        if (it.risk === 'R0' || it.risk === 'R1') S.selection.add(it.id);
      });
      renderItemTable();
      updateSelectionBar();
    };
    $('#btnClearSel').onclick = function () {
      S.selection.clear();
      renderItemTable();
      updateSelectionBar();
    };
    $('#btnMakePlan').onclick = makePlan;

    // 表格勾选 / 行点击
    $('#itemTableWrap').addEventListener('change', function (e) {
      var cb = e.target.closest('.check');
      if (!cb) return;
      toggleSelect(Number(cb.getAttribute('data-id')), cb.checked, cb);
    });
    $('#itemTableWrap').addEventListener('click', function (e) {
      var row = e.target.closest('tr[data-item]');
      if (!row || e.target.closest('.check')) return;
      if (row.classList.contains('protected')) return;
      var cb = $('.check', row);
      if (!cb) return;
      cb.checked = !cb.checked;
      toggleSelect(Number(cb.getAttribute('data-id')), cb.checked, cb);
    });

    $('#btnEmptyRecycle').onclick = function () {
      if (!S.deviceId) return;
      if (!S.recycleItems.length) return toast('回收站已经是空的');
      confirmModal('清空回收站', '将永久删除回收站中的 ' + S.recycleItems.length + ' 个文件，此操作不可恢复。', function () {
        CP.emptyRecycle(S.deviceId).then(function (r) {
          toast('已释放 ' + fmt(r.freed), 'success');
          loadRecycle();
          refreshOverview();
        }).catch(function (err) { toast(err.message, 'error'); });
      });
    };

    $('#recycleList').addEventListener('click', function (e) {
      var btn = e.target.closest('[data-recycle-act]');
      if (!btn) return;
      var id = Number(btn.getAttribute('data-id'));
      var name = btn.getAttribute('data-name') || '该文件';
      var act = btn.getAttribute('data-recycle-act');
      if (act === 'restore') {
        btn.disabled = true;
        CP.restore(id).then(function (r) {
          toast('已恢复到原路径', 'success');
          loadRecycle();
          refreshOverview();
        }).catch(function (err) {
          btn.disabled = false;
          toast(err.message, 'error');
        });
      } else if (act === 'delete') {
        confirmModal('永久删除', '确认永久删除「' + name + '」？该操作不可恢复。', function () {
          CP.deleteRecycle(id).then(function () {
            toast('已永久删除');
            loadRecycle();
            refreshOverview();
          }).catch(function (err) { toast(err.message, 'error'); });
        });
      }
    });

    $('#btnRefreshReports').onclick = loadReports;
    $('#reportList').addEventListener('click', function (e) {
      var head = e.target.closest('.task-head');
      if (!head || e.target.closest('button')) return;
      head.parentElement.classList.toggle('open');
    });

    // AI 抽屉
    $('#aiFab').onclick = openAiDrawer;
    $('#aiClose').onclick = function () { $('#aiDrawer').classList.add('hidden'); };
    $('#aiQuick').addEventListener('click', function (e) {
      var b = e.target.closest('[data-prompt]');
      if (!b) return;
      $('#aiInput').value = b.getAttribute('data-prompt');
      sendChat();
    });
    $('#aiForm').onsubmit = function (e) {
      e.preventDefault();
      sendChat();
    };

    // 弹窗关闭
    $('#modalRoot').addEventListener('click', function (e) {
      if (e.target.closest('[data-close]') || e.target.classList.contains('modal-overlay')) {
        closeModal();
      }
    });
  }

  function switchAuthTab(login) {
    $('#tabLogin').classList.toggle('active', login);
    $('#tabRegister').classList.toggle('active', !login);
    $('#loginForm').classList.toggle('hidden', !login);
    $('#registerForm').classList.toggle('hidden', login);
    $('#loginError').textContent = '';
  }

  /* ---------------- 视图 ---------------- */
  function switchView(view) {
    S.view = view;
    $$('.view').forEach(function (v) { v.classList.add('hidden'); });
    $('#view-' + view).classList.remove('hidden');
    $$('.nav-item').forEach(function (n) {
      n.classList.toggle('active', n.getAttribute('data-view') === view);
    });
    var meta = VIEW_META[view] || VIEW_META.overview;
    $('#pageTitle').textContent = meta.title;
    $('#pageSub').textContent = meta.sub;
    if (view === 'recycle') loadRecycle();
    if (view === 'reports') loadReports();
    if (view === 'overview') refreshOverview();
  }

  function refreshCurrentView(showToast) {
    if (!S.deviceId) {
      if (showToast) toast('请先添加一个设备');
      return;
    }
    if (S.view === 'recycle') {
      loadRecycle(true);
    } else if (S.view === 'reports') {
      loadReports(true);
    } else {
      loadDevice(true);
    }
    if (showToast) toast('已刷新');
  }

  /* ---------------- 设备 ---------------- */
  function loadDevices() {
    CP.devices().then(function (data) {
      S.devices = data.devices || [];
      renderDeviceSelect();
      if (!S.devices.length) {
        S.deviceId = 0;
        showOverviewEmpty();
        return;
      }
      var stored = CP.deviceId.get();
      var target = S.devices.filter(function (d) { return d.id === stored; })[0] || S.devices[0];
      S.deviceId = target.id;
      CP.deviceId.set(S.deviceId);
      $('#deviceSelect').value = String(S.deviceId);
      loadDevice();
    }).catch(function (err) { toast(err.message, 'error'); });
  }

  function renderDeviceSelect() {
    var sel = $('#deviceSelect');
    if (!S.devices.length) {
      sel.innerHTML = '<option value="">暂无设备</option>';
      return;
    }
    sel.innerHTML = S.devices.map(function (d) {
      var warn = d.exists ? '' : '（路径不可用）';
      return '<option value="' + d.id + '">' + esc(d.name) + warn + '</option>';
    }).join('');
  }

  function resetDeviceState() {
    stopTimers();
    S.overview = null;
    S.analysis = null;
    S.scan = null;
    S.items = [];
    S.selection.clear();
    S.riskFilter = 'all';
    S.catFilter = 'all';
    S.search = '';
    $('#itemSearch').value = '';
  }

  function loadDevice(silent) {
    if (!S.deviceId) { showOverviewEmpty(); return; }
    Promise.all([CP.overview(S.deviceId), CP.analysis(S.deviceId)]).then(function (res) {
      S.overview = res[0];
      S.analysis = res[1];
      renderOverview();
      var last = S.overview.last_scan;
      if (last && (last.status === 'done' || last.status === 'running')) {
        loadScan(last.id, true);
      } else {
        S.scan = null;
        S.items = [];
        S.selection.clear();
        renderScanPanel();
        renderChips();
        renderItemTable();
        updateSelectionBar();
      }
    }).catch(function (err) {
      if (!silent) toast(err.message, 'error');
      showOverviewEmpty();
    });
  }

  function showOverviewEmpty() {
    $('#overviewEmpty').classList.remove('hidden');
    $('#overviewContent').classList.add('hidden');
    $('#selectionBar').classList.add('hidden');
    $('#scanStatusCard').innerHTML = '<p class="muted">请先添加设备后再开始扫描。</p>';
    $('#itemTableWrap').innerHTML = '<div class="table-empty">暂无数据</div>';
  }

  /* ---------------- 总览渲染 ---------------- */
  function renderOverview() {
    if (!S.overview) return;
    $('#overviewEmpty').classList.add('hidden');
    $('#overviewContent').classList.remove('hidden');
    var ov = S.overview;
    var dev = ov.device;
    var st = ov.storage;

    $('#storageDeviceName').textContent = dev.name;
    $('#deviceMeta').textContent = dev.root_path + ' · 保留 ' + dev.retention_days + ' 天 · 风险上限 ' + dev.max_risk;
    $('#storagePct').textContent = (st.percent || 0) + '%';
    $('#storageUsed').textContent = fmt(st.used);
    $('#storageFree').textContent = fmt(st.free);
    $('#storageTotal').textContent = fmt(st.total);
    var C = 2 * Math.PI * 52;
    $('#ringStroke').style.strokeDashoffset = String(C * (1 - Math.min(100, st.percent || 0) / 100));

    var pill = $('#healthPill');
    var score = ov.health || 0;
    pill.textContent = '健康分 ' + score;
    pill.className = 'pill ' + (score >= 85 ? 'good' : (score >= 70 ? 'warn' : 'bad'));

    $('#statCleanable').textContent = fmt(ov.cleanable.bytes);
    $('#statCleanableSub').textContent = ov.cleanable.count + ' 项可安全处理';
    $('#statRecycle').textContent = fmt(ov.recycle.bytes);
    $('#statRecycleSub').textContent = ov.recycle.count + ' 个文件待恢复';
    $('#statReleased').textContent = fmt(ov.lifetime.released);
    $('#statReleasedSub').textContent = ov.lifetime.tasks + ' 次清理任务';
    if (ov.last_scan) {
      var ls = ov.last_scan;
      var stateText = ls.status === 'done' ? '已完成' : (ls.status === 'running' ? '进行中' : '未完成');
      $('#statScan').textContent = stateText;
      $('#statScanSub').textContent = CP.fmtTime(ls.finished_at || ls.started_at) + ' · ' + (ls.item_count || 0) + ' 项';
    } else {
      $('#statScan').textContent = '未扫描';
      $('#statScanSub').textContent = '点击开始扫描';
    }

    renderSuggestions();
    renderRiskBreakdown();
    renderCategoryBars();
  }

  function refreshOverview() {
    if (!S.deviceId) return;
    Promise.all([CP.overview(S.deviceId), CP.analysis(S.deviceId)]).then(function (res) {
      S.overview = res[0];
      S.analysis = res[1];
      renderOverview();
    }).catch(function () { });
  }

  function renderSuggestions() {
    var box = $('#suggestions');
    if (!S.analysis) {
      box.innerHTML = '<li class="muted">暂无分析数据</li>';
      return;
    }
    var list = S.analysis.suggestions || [];
    if (!list.length) list = ['设备状态良好，暂无需紧急清理的内容'];
    box.innerHTML = list.map(function (s) { return '<li>' + esc(s) + '</li>'; }).join('');
  }

  function riskStats() {
    if (S.scan && S.scan.stats && Object.keys(S.scan.stats).length) return S.scan.stats;
    if (S.overview && S.overview.last_scan && S.overview.last_scan.stats_json) {
      try { return JSON.parse(S.overview.last_scan.stats_json); } catch (e) { return {}; }
    }
    return {};
  }

  function renderRiskBreakdown() {
    var stats = riskStats();
    var keys = ['R0', 'R1', 'R2', 'R3'];
    var max = 1;
    keys.forEach(function (k) { max = Math.max(max, (stats[k] || {}).bytes || 0); });
    var total = 0;
    keys.forEach(function (k) { total += (stats[k] || {}).count || 0; });
    $('#riskHint').textContent = total ? '最近扫描共 ' + total + ' 项' : '暂无扫描数据';
    $('#riskBreakdown').innerHTML = keys.map(function (k) {
      var info = stats[k] || { count: 0, bytes: 0 };
      var w = Math.max(2, Math.round((info.bytes || 0) * 100 / max));
      var color = CP.riskColor[k];
      return '<div class="risk-row">' +
        '<span class="badge risk-' + k + '">' + k + '</span>' +
        '<div class="bar"><i style="width:' + w + '%;background:' + color + '"></i></div>' +
        '<span class="cnt">' + info.count + ' 项</span>' +
        '</div>';
    }).join('');
  }

  function renderCategoryBars() {
    var box = $('#categoryBars');
    if (!S.analysis) { box.innerHTML = '<p class="muted small">暂无数据</p>'; return; }
    var cats = Object.keys(S.analysis.categories || {}).map(function (k) {
      var v = S.analysis.categories[k];
      return { label: k, count: v.count, bytes: v.bytes, risks: v.risks || {} };
    }).filter(function (c) {
      var onlyProtected = Object.keys(c.risks).every(function (r) { return r === 'R3'; });
      return !onlyProtected && c.bytes > 0;
    }).sort(function (a, b) { return b.bytes - a.bytes; }).slice(0, 6);
    if (!cats.length) { box.innerHTML = '<p class="muted small">暂无清理类别</p>'; return; }
    var max = cats[0].bytes || 1;
    box.innerHTML = cats.map(function (c) {
      var w = Math.max(4, Math.round(c.bytes * 100 / max));
      return '<div class="cat-row"><div class="top"><b>' + esc(c.label) + '</b><span>' +
        fmt(c.bytes) + ' · ' + c.count + ' 项</span></div>' +
        '<div class="track"><i style="width:' + w + '%"></i></div></div>';
    }).join('');
  }

  /* ---------------- 扫描 ---------------- */
  function loadScan(id, keepFilter) {
    Promise.all([CP.scan(id), CP.scanItems(id)]).then(function (res) {
      var scan = res[0].scan;
      var items = res[1].items || [];
      S.scan = scan;
      S.items = items;
      if (!keepFilter) {
        S.riskFilter = 'all';
        S.catFilter = 'all';
      }
      S.selection = new Set(items.filter(function (it) {
        return it.default_selected && it.risk !== 'R3';
      }).map(function (it) { return it.id; }));
      renderScanPanel();
      renderChips();
      renderItemTable();
      updateSelectionBar();
      if (scan.status === 'running') startScanPolling();
    }).catch(function (err) { toast(err.message, 'error'); });
  }

  function stopScanPolling() {
    if (S.scanTimer) { clearInterval(S.scanTimer); S.scanTimer = null; }
  }

  function stopTimers() {
    stopScanPolling();
    if (S.taskTimer) { clearInterval(S.taskTimer); S.taskTimer = null; }
  }

  function startScanPolling() {
    if (!S.scan) return;
    stopScanPolling();
    var sid = S.scan.id;
    S.scanTimer = setInterval(function () {
      CP.scan(sid).then(function (data) {
        S.scan = data.scan;
        renderScanPanel();
        if (data.scan.status === 'done') {
          stopScanPolling();
          loadScan(sid);
          refreshOverview();
        } else if (data.scan.status === 'failed') {
          stopScanPolling();
          toast('扫描失败：' + (data.scan.error || '未知错误'), 'error');
        }
      }).catch(function (err) {
        stopScanPolling();
        toast(err.message, 'error');
      });
    }, 900);
  }

  function startScan() {
    if (!S.deviceId) { toast('请先添加一个设备'); return; }
    CP.startScan(S.deviceId).then(function (r) {
      S.scan = {
        id: r.scan_id, status: 'running', phase: 'walking', progress: 0,
        file_count: 0, total_bytes: 0, item_count: 0, started_at: Date.now() / 1000
      };
      S.items = [];
      S.selection.clear();
      renderScanPanel();
      renderChips();
      renderItemTable();
      updateSelectionBar();
      switchView('scan');
      startScanPolling();
      toast('扫描已开始，正在分析文件');
    }).catch(function (err) { toast(err.message, 'error'); });
  }

  function renderScanPanel() {
    var box = $('#scanStatusCard');
    var s = S.scan;
    var icon = '<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.8">' +
      '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5 20 20"/><path d="M8 10.5h5"/></svg>';

    if (!s) {
      box.innerHTML =
        '<div class="scan-orb">' + icon + '</div>' +
        '<div class="scan-main"><h3>尚未执行扫描</h3><p>扫描会遍历设备文件，按 R0-R3 分级识别可清理内容，全程只读不会改动文件。</p></div>' +
        '<div class="scan-side"><button class="btn primary" data-scan-action="start">开始扫描</button></div>';
      return;
    }

    if (s.status === 'running') {
      var phase = s.phase === 'analyzing' ? '正在分析重复与风险' : '正在遍历文件';
      box.innerHTML =
        '<div class="scan-orb spin"><svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.8">' +
        '<path d="M20 12a8 8 0 1 1-3-6.2"/><path d="M20 4v5h-5"/></svg></div>' +
        '<div class="scan-main"><h3>' + phase + '</h3><p>已遍历 ' + (s.file_count || 0) + ' 个文件 · 共 ' + fmt(s.total_bytes) + '</p>' +
        '<div class="scan-progress"><i style="width:' + (s.progress || 0) + '%"></i></div></div>' +
        '<div class="scan-side"><b>' + (s.progress || 0) + '%</b><span>请稍候</span></div>';
      return;
    }

    if (s.status === 'failed') {
      box.innerHTML =
        '<div class="scan-orb">' + icon + '</div>' +
        '<div class="scan-main"><h3>扫描失败</h3><p>' + esc(s.error || '未知错误') + '</p></div>' +
        '<div class="scan-side"><button class="btn primary" data-scan-action="start">重新扫描</button></div>';
      return;
    }

    box.innerHTML =
      '<div class="scan-orb">' + icon + '</div>' +
      '<div class="scan-main"><h3>扫描完成</h3><p>共遍历 ' + (s.file_count || 0) + ' 个文件，发现 ' +
      (s.item_count || 0) + ' 个可处理项 · ' + CP.fmtTime(s.finished_at || s.started_at) + '</p></div>' +
      '<div class="scan-side"><button class="btn ghost" data-scan-action="start">重新扫描</button></div>';
  }

  function renderChips() {
    var stats = {};
    S.items.forEach(function (it) {
      stats[it.risk] = (stats[it.risk] || 0) + 1;
    });
    var risks = [
      { key: 'all', label: '全部' },
      { key: 'R0', label: 'R0 安全清理' },
      { key: 'R1', label: 'R1 建议清理' },
      { key: 'R2', label: 'R2 需谨慎' },
      { key: 'R3', label: 'R3 受保护' }
    ];
    $('#riskChips').innerHTML = risks.map(function (r) {
      var n = r.key === 'all' ? S.items.length : (stats[r.key] || 0);
      return '<button class="chip' + (S.riskFilter === r.key ? ' active' : '') + '" data-risk="' + r.key + '">' +
        r.label + ' <small>' + n + '</small></button>';
    }).join('');

    var catCounts = {};
    S.items.forEach(function (it) {
      catCounts[it.category_label] = (catCounts[it.category_label] || 0) + 1;
    });
    var cats = Object.keys(catCounts).sort(function (a, b) { return catCounts[b] - catCounts[a]; });
    var html = '<button class="chip' + (S.catFilter === 'all' ? ' active' : '') + '" data-cat="all">全部类别</button>';
    html += cats.map(function (c) {
      return '<button class="chip' + (S.catFilter === c ? ' active' : '') + '" data-cat="' + esc(c) + '">' +
        esc(c) + ' <small>' + catCounts[c] + '</small></button>';
    }).join('');
    $('#categoryChips').innerHTML = html;
  }

  function renderItemTable() {
    var wrap = $('#itemTableWrap');
    if (!S.items.length) {
      wrap.innerHTML = '<div class="table-empty">' +
        (S.scan && S.scan.status === 'running' ? '正在扫描，请稍候…' : '暂无扫描结果，点击上方「开始扫描」开始分析。') +
        '</div>';
      return;
    }
    var list = S.items.filter(function (it) {
      if (S.riskFilter !== 'all' && it.risk !== S.riskFilter) return false;
      if (S.catFilter !== 'all' && it.category_label !== S.catFilter) return false;
      if (S.search) {
        var hay = (it.name + ' ' + it.path).toLowerCase();
        if (hay.indexOf(S.search) < 0) return false;
      }
      return true;
    });
    if (!list.length) {
      wrap.innerHTML = '<div class="table-empty">当前筛选条件下没有匹配的文件</div>';
      return;
    }
    var shown = list.slice(0, 300);
    var rows = shown.map(function (it) {
      var disabled = it.risk === 'R3';
      var checked = S.selection.has(it.id);
      return '<tr data-item="' + it.id + '" class="' + (checked ? 'selected ' : '') + (disabled ? 'protected' : '') + '">' +
        '<td><input type="checkbox" class="check" data-id="' + it.id + '"' + (checked ? ' checked' : '') + (disabled ? ' disabled' : '') + '></td>' +
        '<td class="cell-file"><div class="file-name" title="' + esc(it.name) + '">' + esc(it.name) + '</div>' +
        '<div class="file-path" title="' + esc(it.path) + '">' + esc(it.path) + '</div></td>' +
        '<td>' + esc(it.category_label) + '</td>' +
        '<td><span class="badge risk-' + it.risk + '">' + it.risk + ' ' + CP.riskLabel[it.risk] + '</span></td>' +
        '<td class="cell-size">' + fmt(it.size) + '</td>' +
        '<td class="cell-reason">' + esc(it.reason) + '</td>' +
        '</tr>';
    }).join('');
    var more = list.length > shown.length ?
      '<div class="table-empty">共 ' + list.length + ' 项，仅显示前 ' + shown.length + ' 项，请使用搜索缩小范围。</div>' : '';
    wrap.innerHTML = '<table class="data-table"><thead><tr>' +
      '<th style="width:36px"></th><th>文件</th><th>类别</th><th>风险</th><th>大小</th><th>判断依据</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>' + more;
  }

  function toggleSelect(id, checked, cb) {
    if (checked) S.selection.add(id); else S.selection.delete(id);
    var row = cb.closest('tr');
    if (row) row.classList.toggle('selected', checked);
    updateSelectionBar();
  }

  function updateSelectionBar() {
    var bar = $('#selectionBar');
    var count = S.selection.size;
    if (!count || !S.items.length) {
      bar.classList.add('hidden');
    } else {
      bar.classList.remove('hidden');
      var bytes = 0;
      S.items.forEach(function (it) { if (S.selection.has(it.id)) bytes += it.size; });
      $('#selCount').textContent = count;
      $('#selBytes').textContent = fmt(bytes);
    }
  }

  /* ---------------- 清理方案 ---------------- */
  function makePlan() {
    if (!S.scan || S.scan.status !== 'done') { toast('请先完成一次扫描'); return; }
    if (!S.selection.size) { toast('请先选择要清理的文件'); return; }
    var btn = $('#btnMakePlan');
    btn.disabled = true;
    CP.createPlan(S.scan.id, Array.from(S.selection)).then(function (data) {
      openPlanModal(data);
    }).catch(function (err) {
      toast(err.message, 'error');
      if (err.data && err.data.blocked) toast('部分文件超过当前设备的风险上限', 'error');
    }).then(function () { btn.disabled = false; });
  }

  function openPlanModal(plan) {
    var order = ['R0', 'R1', 'R2', 'R3'];
    var lines = order.filter(function (r) { return plan.by_risk[r]; }).map(function (r) {
      var info = plan.by_risk[r];
      return '<div class="plan-line"><span><span class="badge risk-' + r + '">' + r + '</span> ' +
        CP.riskLabel[r] + '</span><span>' + info.count + ' 项 · <b>' + fmt(info.bytes) + '</b></span></div>';
    }).join('');
    var hasR2 = !!plan.by_risk.R2;
    var html = '<div class="modal-overlay">' +
      '<div class="modal" data-modal="plan">' +
      '<div class="modal-head"><h3>确认清理方案</h3><button class="icon-btn" data-close>×</button></div>' +
      '<div class="modal-body">' +
      '<p class="muted">共 ' + plan.total_items + ' 项，合计 <b>' + fmt(plan.total_bytes) + '</b>。文件不会直接删除，而是移入回收站，保留 ' + plan.retention_days + ' 天内可恢复。</p>' +
      '<div class="plan-summary">' + lines + '</div>' +
      (hasR2 ? '<label class="confirm-check"><input type="checkbox" id="ackR2"><span>方案中包含 <b>R2 需谨慎</b> 内容（旧文档、压缩包、大文件等），请确认这些文件已不再需要。</span></label>' : '') +
      '</div>' +
      '<div class="modal-foot"><button class="btn ghost" data-close>取消</button>' +
      '<button class="btn primary" id="btnExecute" disabled>' + (hasR2 ? '需勾选确认后执行' : '确认执行清理') + '</button></div>' +
      '</div></div>';
    $('#modalRoot').innerHTML = html;
    var ackBox = $('#ackR2');
    var exec = $('#btnExecute');
    if (!hasR2) exec.disabled = false;
    if (ackBox) {
      ackBox.onchange = function () {
        exec.disabled = !ackBox.checked;
        exec.textContent = ackBox.checked ? '确认执行清理' : '需勾选确认后执行';
      };
    }
    exec.onclick = function () {
      exec.disabled = true;
      CP.execute(plan.plan_id, ackBox ? ackBox.checked : false).then(function (r) {
        closeModal();
        openTaskModal(r.task_id);
        startTaskPolling(r.task_id);
      }).catch(function (err) {
        exec.disabled = false;
        toast(err.message, 'error');
      });
    };
  }

  function openTaskModal(taskId) {
    $('#modalRoot').innerHTML = '<div class="modal-overlay">' +
      '<div class="modal" data-modal="task">' +
      '<div class="modal-head"><h3>正在清理</h3></div>' +
      '<div class="modal-body">' +
      '<p class="muted" id="taskStateText">文件正在移入回收站…</p>' +
      '<div class="task-progress-wrap"><div class="task-progress"><i id="taskBar" style="width:0%"></i></div>' +
      '<div class="task-progress-meta"><span id="taskPct">0%</span><span id="taskCounts">已处理 0 项</span></div></div>' +
      '</div></div></div>';
  }

  function startTaskPolling(taskId) {
    if (S.taskTimer) { clearInterval(S.taskTimer); S.taskTimer = null; }
    var tick = function () {
      CP.task(taskId).then(function (data) {
        var t = data.task;
        var bar = $('#taskBar');
        if (!bar) { clearInterval(S.taskTimer); S.taskTimer = null; return; }
        bar.style.width = (t.progress || 0) + '%';
        $('#taskPct').textContent = (t.progress || 0) + '%';
        $('#taskCounts').textContent = '已移动 ' + t.moved_items + ' 项 · 失败 ' + t.failed_items + ' 项 · 释放 ' + fmt(t.released_bytes);
        if (t.state === 'completed' || t.state === 'partial' || t.state === 'failed') {
          clearInterval(S.taskTimer);
          S.taskTimer = null;
          renderTaskResult(t);
          refreshOverview();
          if (S.view === 'recycle') loadRecycle();
          if (S.view === 'reports') loadReports();
        }
      }).catch(function (err) {
        clearInterval(S.taskTimer);
        S.taskTimer = null;
        toast(err.message, 'error');
      });
    };
    tick();
    S.taskTimer = setInterval(tick, 800);
  }

  function renderTaskResult(t) {
    var ok = t.state === 'completed';
    var title = ok ? '清理完成' : (t.state === 'partial' ? '部分完成' : '清理失败');
    var colors = { completed: 'var(--green)', partial: 'var(--orange)', failed: 'var(--red)' };
    var html = '<div class="modal-overlay">' +
      '<div class="modal" data-modal="result">' +
      '<div class="modal-head"><h3>' + title + '</h3><button class="icon-btn" data-close>×</button></div>' +
      '<div class="modal-body">' +
      '<p class="muted">文件已移入回收站，可在保留期内恢复；释放的空间将在清空回收站后生效。</p>' +
      '<div class="result-grid">' +
      '<div><b style="color:' + colors[t.state] + '">' + t.moved_items + '</b><span>成功移动</span></div>' +
      '<div><b>' + t.failed_items + '</b><span>失败</span></div>' +
      '<div><b>' + fmt(t.released_bytes) + '</b><span>已释放</span></div>' +
      '</div></div>' +
      '<div class="modal-foot"><button class="btn ghost" data-close>关闭</button>' +
      '<button class="btn ghost" id="goReports">查看报告</button>' +
      '<button class="btn primary" id="goRecycle">打开回收站</button></div>' +
      '</div></div>';
    $('#modalRoot').innerHTML = html;
    $('#goRecycle').onclick = function () { closeModal(); switchView('recycle'); };
    $('#goReports').onclick = function () { closeModal(); switchView('reports'); };
  }

  /* ---------------- 回收站 ---------------- */
  function loadRecycle(silent) {
    if (!S.deviceId) return;
    CP.recycle(S.deviceId).then(function (data) {
      S.recycleItems = data.items || [];
      $('#recycleSummary').textContent = '占用 ' + fmt(data.occupied) + ' · 保留 ' + data.retention_days + ' 天';
      $('#btnEmptyRecycle').disabled = !S.recycleItems.length;
      var box = $('#recycleList');
      if (!S.recycleItems.length) {
        box.innerHTML = '<div class="empty-list">回收站是空的<br><span class="small">清理的文件会暂存在这里，随时可恢复</span></div>';
        return;
      }
      box.innerHTML = S.recycleItems.map(function (it) {
        return '<div class="list-row">' +
          '<div class="file-ico">▤</div>' +
          '<div class="list-main"><div class="file-name">' + esc(it.name) + '</div>' +
          '<div class="list-meta" title="' + esc(it.orig_path) + '">原位置：' + esc(it.orig_path) + '</div>' +
          '<div class="list-meta">' + fmt(it.size) + ' · 删除于 ' + CP.fmtTime(it.deleted_at) +
          ' · 剩余 ' + it.days_left + ' 天' + (it.exists ? '' : ' · <span style="color:var(--red)">备份缺失</span>') + '</div></div>' +
          '<div class="list-actions">' +
          '<button class="btn ghost small" data-recycle-act="restore" data-id="' + it.id + '"' + (it.exists ? '' : ' disabled') + '>恢复</button>' +
          '<button class="btn ghost small danger" data-recycle-act="delete" data-id="' + it.id + '" data-name="' + esc(it.name) + '">永久删除</button>' +
          '</div></div>';
      }).join('');
    }).catch(function (err) {
      if (!silent) toast(err.message, 'error');
    });
  }

  /* ---------------- 报告 ---------------- */
  function loadReports(silent) {
    if (!S.deviceId) return;
    CP.reports(S.deviceId).then(function (data) {
      var tasks = data.tasks || [];
      var box = $('#reportList');
      if (!tasks.length) {
        box.innerHTML = '<div class="empty-list">暂无清理记录，完成一次清理后会在这里生成报告</div>';
        return;
      }
      var stateLabel = { completed: '已完成', partial: '部分完成', failed: '失败', running: '执行中' };
      box.innerHTML = tasks.map(function (t) {
        var details = (t.detail || []).map(function (d) {
          return '<tr><td>' + esc(d.name) + '</td><td>' + fmt(d.size) + '</td><td>' +
            (d.ok ? '已移动' : ('失败：' + esc(d.reason || '未知'))) + '</td></tr>';
        }).join('');
        return '<div class="task-card">' +
          '<div class="task-head"><span class="task-id">#' + t.id + '</span>' +
          '<span class="state-tag state-' + t.state + '">' + (stateLabel[t.state] || t.state) + '</span>' +
          '<div class="task-stats"><span>移动 ' + t.moved_items + ' 项</span><span>失败 ' + t.failed_items + ' 项</span>' +
          '<span>释放 ' + fmt(t.released_bytes) + '</span><span>' + CP.fmtTime(t.started_at) + '</span></div>' +
          '<div class="spacer"></div><span class="muted small">展开明细</span></div>' +
          '<div class="task-detail"><table class="detail-table"><tbody>' +
          (details || '<tr><td colspan="3">暂无明细</td></tr>') + '</tbody></table></div>' +
          '</div>';
      }).join('');
    }).catch(function (err) {
      if (!silent) toast(err.message, 'error');
    });
  }

  /* ---------------- AI 助手 ---------------- */
  function openAiDrawer() {
    if (!CP.token.get()) return;
    $('#aiDrawer').classList.remove('hidden');
    if (!S.chatStarted) {
      S.chatStarted = true;
      addBotMessage('您好，我是 CleanPilot AI 助手。我可以分析设备存储状态、解释清理安全性，并帮你找出最值得清理的内容。<br>试试问我「太卡了怎么办」或「清理缓存」。', []);
    }
  }

  function addBotMessage(html, actions) {
    var box = $('#aiMessages');
    var el = document.createElement('div');
    el.className = 'msg bot';
    el.innerHTML = html + (actions && actions.length ? '<div class="msg-actions">' +
      actions.map(function (a, i) {
        return '<button class="btn ghost small" data-ai-action="' + a.type + '" data-ai-filter="' + (a.filter || '') + '">' + esc(a.label) + '</button>';
      }).join('') + '</div>' : '');
    box.appendChild(el);
    el.querySelectorAll('[data-ai-action]').forEach(function (btn) {
      btn.onclick = function () {
        var type = btn.getAttribute('data-ai-action');
        var filter = btn.getAttribute('data-ai-filter');
        $('#aiDrawer').classList.add('hidden');
        switchView('scan');
        if (type === 'scan') {
          if (!S.scan || S.scan.status !== 'done') startScan();
        } else if (type === 'plan' && filter) {
          var label = CATEGORY_BY_FILTER[filter];
          if (label) {
            S.catFilter = label;
            renderChips();
            renderItemTable();
          }
        }
      };
    });
    box.scrollTop = box.scrollHeight;
  }

  function addUserMessage(text) {
    var box = $('#aiMessages');
    var el = document.createElement('div');
    el.className = 'msg user';
    el.textContent = text;
    box.appendChild(el);
    box.scrollTop = box.scrollHeight;
  }

  function sendChat() {
    var input = $('#aiInput');
    var msg = input.value.trim();
    if (!msg) return;
    if (!S.deviceId) { toast('请先添加一个设备'); return; }
    addUserMessage(msg);
    input.value = '';
    var form = $('#aiForm');
    var btn = $('button', form);
    btn.disabled = true;
    CP.chat(S.deviceId, msg).then(function (r) {
      var actions = [];
      if (r.action === 'scan') actions.push({ type: 'scan', label: '立即扫描' });
      if (r.action === 'plan') actions.push({ type: 'plan', label: '查看可清理项', filter: r.filter || '' });
      addBotMessage(r.reply, actions);
    }).catch(function (err) {
      addBotMessage('抱歉，AI 服务暂时不可用：' + esc(err.message), []);
    }).then(function () { btn.disabled = false; });
  }

  /* ---------------- 添加设备 ---------------- */
  function openAddDeviceModal() {
    var html = '<div class="modal-overlay"><div class="modal" data-modal="add-device">' +
      '<div class="modal-head"><h3>添加设备</h3><button class="icon-btn" data-close>×</button></div>' +
      '<div class="modal-body"><form class="modal-form" id="addDeviceForm">' +
      '<label>设备名称<input id="devName" placeholder="例如：我的笔记本" required></label>' +
      '<label>存储路径<input id="devPath" placeholder="例如：C:\\Users\\你的用户名\\Downloads" required>' +
      '<span class="hint">填入一个真实存在的本地文件夹，系统会扫描该文件夹内的文件。</span></label>' +
      '<div class="modal-foot" style="padding:4px 0 0">' +
      '<button type="button" class="btn ghost" data-close>取消</button>' +
      '<button type="submit" class="btn primary">添加并扫描</button></div>' +
      '</form></div></div></div>';
    $('#modalRoot').innerHTML = html;
    $('#addDeviceForm').onsubmit = function (e) {
      e.preventDefault();
      var name = $('#devName').value.trim();
      var path = $('#devPath').value.trim();
      var submit = $('button[type=submit]', e.target);
      submit.disabled = true;
      CP.addDevice(name, path).then(function (r) {
        closeModal();
        toast('设备已添加', 'success');
        loadDevices();
      }).catch(function (err) {
        submit.disabled = false;
        toast(err.message, 'error');
      });
    };
  }

  /* ---------------- 弹窗 / Toast 通用 ---------------- */
  function confirmModal(title, text, onOk) {
    var html = '<div class="modal-overlay"><div class="modal" style="max-width:420px">' +
      '<div class="modal-head"><h3>' + esc(title) + '</h3><button class="icon-btn" data-close>×</button></div>' +
      '<div class="modal-body"><p class="muted">' + esc(text) + '</p></div>' +
      '<div class="modal-foot"><button class="btn ghost" data-close>取消</button>' +
      '<button class="btn primary" id="confirmOk">确认</button></div></div></div>';
    $('#modalRoot').innerHTML = html;
    $('#confirmOk').onclick = function () {
      closeModal();
      onOk();
    };
  }

  function closeModal() {
    $('#modalRoot').innerHTML = '';
  }

  function toast(message, type) {
    var root = $('#toastRoot');
    var el = document.createElement('div');
    el.className = 'toast' + (type ? ' ' + type : '');
    el.textContent = message;
    root.appendChild(el);
    setTimeout(function () {
      el.style.opacity = '0';
      el.style.transform = 'translateY(8px)';
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
