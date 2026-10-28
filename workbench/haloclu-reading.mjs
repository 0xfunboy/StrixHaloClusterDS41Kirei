const FOLLOW_THRESHOLD = 64;

function objectFromStorage(name) {
  try {
    const value = JSON.parse(sessionStorage.getItem(name) || '{}');
    return value && typeof value === 'object' ? value : {};
  } catch {
    return {};
  }
}

export function createReadingController({ projectId, language }) {
  const readingName = 'haloclu.readingState.v1';
  const draftName = 'haloclu.drafts.v1';
  const reading = objectFromStorage(readingName);
  const drafts = objectFromStorage(draftName);

  const key = (kind, ...parts) => [kind, ...parts.map(value => String(value ?? ''))].join('/');
  const writeStorage = (name, value, limit) => {
    try {
      const rows = Object.entries(value)
        .sort((a, b) => Number(b[1]?.updated || 0) - Number(a[1]?.updated || 0))
        .slice(0, limit);
      sessionStorage.setItem(name, JSON.stringify(Object.fromEntries(rows)));
    } catch {
      // Private mode and quota failures must never break generation.
    }
  };
  const get = (name, defaults = {}) => ({ ...defaults, ...(reading[name] || {}) });
  const set = (name, patch) => {
    reading[name] = { ...(reading[name] || {}), ...patch, updated: Date.now() };
    writeStorage(readingName, reading, 256);
    return reading[name];
  };
  const selectionWithin = node => {
    const selection = window.getSelection?.();
    return Boolean(selection && selection.rangeCount && !selection.isCollapsed && node
      && (node.contains(selection.anchorNode) || node.contains(selection.focusNode)));
  };
  const focusWithin = node => Boolean(node && document.activeElement && node.contains(document.activeElement));

  const defer = (node, value, renderer) => {
    node.__halocluDeferred = { value, renderer };
    node.dataset.deferredRender = '1';
  };
  const flushDeferred = () => {
    for (const node of document.querySelectorAll('[data-deferred-render="1"]')) {
      if (selectionWithin(node) || focusWithin(node)) continue;
      const item = node.__halocluDeferred;
      if (!item) continue;
      delete node.dataset.deferredRender;
      delete node.__halocluDeferred;
      item.renderer(node, item.value);
    }
  };
  document.addEventListener('selectionchange', () => requestAnimationFrame(flushDeferred));

  const draftKey = id => key('draft', projectId(), id);
  const saveDraft = node => {
    if (!projectId() || !node) return;
    drafts[draftKey(node.id)] = { value: node.value.slice(0, 50000), updated: Date.now() };
    node.dataset.dirty = '1';
    writeStorage(draftName, drafts, 96);
  };
  const clearDraft = id => {
    const node = document.getElementById(id);
    if (node) {
      node.value = '';
      node.dataset.dirty = '0';
      node.dataset.projectId = projectId();
    }
    delete drafts[draftKey(id)];
    writeStorage(draftName, drafts, 96);
  };
  const syncDraft = (id, serverValue = '') => {
    const node = document.getElementById(id);
    if (!node || !projectId()) return;
    const saved = drafts[draftKey(id)];
    if (node.dataset.projectId !== projectId()) {
      node.dataset.projectId = projectId();
      node.dataset.dirty = saved ? '1' : '0';
      node.value = saved?.value ?? serverValue;
      return;
    }
    if (!saved && node.dataset.dirty !== '1' && document.activeElement !== node && node.value !== serverValue) {
      node.value = serverValue;
    }
  };
  const clearSavedDraft = id => {
    const node = document.getElementById(id);
    delete drafts[draftKey(id)];
    if (node) node.dataset.dirty = '0';
    writeStorage(draftName, drafts, 96);
  };

  const panelKey = name => key('panel-scroll', projectId(), name);
  const rememberPanel = (name, panel) => {
    if (panel) set(panelKey(name), { scrollTop: panel.scrollTop });
  };
  const restorePanel = (name, panel) => {
    if (!panel) return;
    const scrollTop = Number(get(panelKey(name), { scrollTop: 0 }).scrollTop || 0);
    requestAnimationFrame(() => { panel.scrollTop = scrollTop; });
  };

  const detailsKey = (job, attempt, panel) => key(
    'details', projectId() || job?.project || '', job?.backend_id || '', job?.id || '', attempt || 'primary', panel,
  );
  const bindDetails = (details, stateKey, defaultOpen = false) => {
    if (!details || !stateKey) return;
    if (details.dataset.stateKey !== stateKey) {
      details.dataset.stateKey = stateKey;
      const saved = get(stateKey, { open: defaultOpen, scrollTop: 0 });
      details.open = Boolean(saved.open);
    }
    if (!details.dataset.stateBound) {
      details.dataset.stateBound = '1';
      details.addEventListener('toggle', () => {
        const body = details.querySelector('.message-content,pre');
        set(details.dataset.stateKey, { open: details.open, scrollTop: body?.scrollTop || 0 });
      });
    }
    const body = details.querySelector('.message-content,pre');
    if (body && !body.dataset.panelScrollBound) {
      body.dataset.panelScrollBound = '1';
      body.addEventListener('scroll', () => set(details.dataset.stateKey, {
        open: details.open,
        scrollTop: body.scrollTop,
      }), { passive: true });
    }
    if (body && details.open) {
      requestAnimationFrame(() => {
        body.scrollTop = Number(get(stateKey, { scrollTop: 0 }).scrollTop || 0);
      });
    }
  };

  const chatKey = () => key('chat-scroll', projectId());
  const nearBottom = root => root.scrollHeight - root.scrollTop - root.clientHeight <= FOLLOW_THRESHOLD;
  const updateFollowButton = (button, value = get(chatKey(), { mode: 'FOLLOWING' })) => {
    if (!button) return;
    const readingMode = value.mode === 'READING';
    button.hidden = !readingMode;
    button.textContent = language() === 'it'
      ? 'Nuovo contenuto · Torna in fondo'
      : 'New content · Follow output';
  };
  const setChatMode = (mode, button, extra = {}) => updateFollowButton(button, set(chatKey(), { mode, ...extra }));
  const bindChat = (root, button) => {
    if (root.dataset.scrollBound) return;
    root.dataset.scrollBound = '1';
    const markIntent = () => {
      root.__userIntentUntil = Date.now() + 900;
      if (!nearBottom(root) || selectionWithin(root)) setChatMode('READING', button, { scrollTop: root.scrollTop });
    };
    for (const name of ['wheel', 'touchmove', 'pointerdown']) root.addEventListener(name, markIntent, { passive: true });
    root.addEventListener('scroll', () => {
      if (root.__programmaticScroll) return;
      if (Date.now() > (root.__userIntentUntil || 0) && !selectionWithin(root)) return;
      setChatMode(nearBottom(root) && !selectionWithin(root) ? 'FOLLOWING' : 'READING', button, { scrollTop: root.scrollTop });
    }, { passive: true });
  };
  const captureChat = (root, button) => {
    bindChat(root, button);
    const saved = get(chatKey(), { mode: 'FOLLOWING' });
    let mode = saved.mode || 'FOLLOWING';
    if (selectionWithin(root)) mode = 'READING';
    if (mode === 'FOLLOWING' && !nearBottom(root)) mode = 'READING';
    let anchor = null;
    let offset = 0;
    if (mode === 'READING') {
      const top = root.getBoundingClientRect().top;
      for (const node of root.querySelectorAll('[data-message-key]')) {
        const box = node.getBoundingClientRect();
        if (box.bottom > top + 1) {
          anchor = node.dataset.messageKey;
          offset = box.top - top;
          break;
        }
      }
    }
    updateFollowButton(button, { mode });
    return { mode, anchor, offset, scrollTop: root.scrollTop };
  };
  const restoreChat = (root, button, snapshot) => {
    root.__programmaticScroll = true;
    if (snapshot.mode === 'FOLLOWING') {
      root.scrollTop = root.scrollHeight;
      setChatMode('FOLLOWING', button, { scrollTop: root.scrollTop });
    } else {
      const anchor = snapshot.anchor
        ? root.querySelector(`[data-message-key="${CSS.escape(snapshot.anchor)}"]`)
        : null;
      if (anchor) {
        const top = root.getBoundingClientRect().top;
        root.scrollTop += anchor.getBoundingClientRect().top - top - snapshot.offset;
      } else {
        root.scrollTop = snapshot.scrollTop;
      }
      setChatMode('READING', button, { scrollTop: root.scrollTop });
    }
    requestAnimationFrame(() => { root.__programmaticScroll = false; });
  };
  const followChat = (root, button) => {
    if (!root) return;
    root.__programmaticScroll = true;
    root.scrollTop = root.scrollHeight;
    setChatMode('FOLLOWING', button, { scrollTop: root.scrollTop });
    requestAnimationFrame(() => { root.__programmaticScroll = false; });
  };

  return {
    key,
    get,
    set,
    selectionWithin,
    focusWithin,
    defer,
    flushDeferred,
    saveDraft,
    clearDraft,
    syncDraft,
    clearSavedDraft,
    rememberPanel,
    restorePanel,
    detailsKey,
    bindDetails,
    captureChat,
    restoreChat,
    followChat,
    updateFollowButton,
  };
}
