(function initMobileSidebarBehaviors() {
  function isMobileView() {
    return window.matchMedia('(max-width: 900px)').matches;
  }

  function closeSidebarIfOpen() {
    if (!isMobileView()) {
      return;
    }

    const sidebar = document.querySelector('[data-sidebar]');
    if (!sidebar || sidebar.getAttribute('data-collapsed') === 'true') {
      return;
    }

    const backdropToggle = document.querySelector('.mobile-sidebar-backdrop[data-sidebar-toggle]');
    if (backdropToggle) {
      backdropToggle.click();
      return;
    }

    // Fallback if backdrop button is unavailable.
    sidebar.setAttribute('data-collapsed', 'true');
    document.querySelectorAll('[data-sidebar-toggle]').forEach((button) => {
      button.setAttribute('aria-pressed', 'true');
      button.setAttribute('aria-label', 'Expand sidebar');
      button.setAttribute('data-direction', 'expand');
    });
  }

  function wireNewChatCloseBehavior() {
    const newChatButton = document.getElementById('new-chat-btn');
    if (!newChatButton) {
      return;
    }

    newChatButton.addEventListener('click', () => {
      // Delay ensures the existing new-chat handler runs first, then drawer closes.
      window.setTimeout(closeSidebarIfOpen, 0);
    });
  }

  function wireConversationSelectCloseBehavior() {
    const conversationList = document.getElementById('conversation-list');
    if (!conversationList) {
      return;
    }

    conversationList.addEventListener('click', (event) => {
      const selectedConversationButton = event.target?.closest?.('.chat-thread__item');
      if (!selectedConversationButton) {
        return;
      }

      // Delay ensures the existing conversation-load handler runs first.
      window.setTimeout(closeSidebarIfOpen, 0);
    });
  }

  function wireMobileSidebarBehaviors() {
    wireNewChatCloseBehavior();
    wireConversationSelectCloseBehavior();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wireMobileSidebarBehaviors, { once: true });
  } else {
    wireMobileSidebarBehaviors();
  }
})();
