"""CNKI search results pagination and sorting via browser automation.

Adapted from cookjohn/cnki-skills cnki-navigate-pages.
"""

from __future__ import annotations

from typing import Literal

NAVIGATE_PAGE_JS = """
async (params) => {
  const action = params.action;

  const cap = document.querySelector('#tcaptcha_transform_dy');
  if (cap && cap.getBoundingClientRect().top >= 0) return { error: 'captcha' };

  const pageLinks = document.querySelectorAll('.pages a');
  const prevMark = document.querySelector('.countPageMark')?.innerText;

  if (action === 'next') {
    const next = Array.from(pageLinks).find(a => a.innerText.trim() === '下一页');
    if (!next) return { error: 'no_next_page' };
    next.click();
  } else if (action === 'previous') {
    const prev = Array.from(pageLinks).find(a => a.innerText.trim() === '上一页');
    if (!prev) return { error: 'no_previous_page' };
    prev.click();
  } else {
    const num = action.replace(/\\D/g, '');
    const target = Array.from(pageLinks).find(a => a.innerText.trim() === num);
    if (!target) return { error: 'page_not_found', available: Array.from(pageLinks).map(a => a.innerText.trim()) };
    target.click();
  }

  await new Promise((resolve, reject) => {
    let n = 0;
    const check = () => {
      const mark = document.querySelector('.countPageMark')?.innerText;
      if (mark && mark !== prevMark) resolve();
      else if (++n > 30) reject('timeout');
      else setTimeout(check, 500);
    };
    setTimeout(check, 1000);
  });

  const cap2 = document.querySelector('#tcaptcha_transform_dy');
  if (cap2 && cap2.getBoundingClientRect().top >= 0) return { error: 'captcha' };

  // Extract results from new page
  const rows = document.querySelectorAll('.result-table-list tbody tr');
  const checkboxes = document.querySelectorAll('.result-table-list tbody input.cbItem');
  const results = Array.from(rows).map((row, i) => {
    const nameCell = row.querySelector('td.name');
    const titleLink = nameCell?.querySelector('a.fz14');
    const authors = Array.from(row.querySelectorAll('td.author a.KnowledgeNetLink') || [])
      .map(a => a.innerText?.trim()).filter(Boolean);
    const journal = row.querySelector('td.source a')?.innerText?.trim() || '';
    const date = row.querySelector('td.date')?.innerText?.trim() || '';
    const database = row.querySelector('td.data')?.innerText?.trim() || '';
    const citations = row.querySelector('td.quote')?.innerText?.trim() || '';
    const downloads = row.querySelector('td.download')?.innerText?.trim() || '';
    return {
      title: titleLink?.innerText?.trim() || '',
      href: titleLink?.href || '',
      exportId: checkboxes[i]?.value || '',
      authors,
      journal,
      date,
      database,
      citations,
      downloads,
      isOnlineFirst: !!nameCell?.querySelector('.marktip'),
    };
  }).filter(r => r.title);

  return {
    action,
    total: document.querySelector('.pagerTitleCell')?.innerText?.match(/([\\d,]+)/)?.[1] || '0',
    page: document.querySelector('.countPageMark')?.innerText || '?',
    results,
    url: location.href
  };
}
"""

SORT_RESULTS_JS = """
async (params) => {
  const sortBy = params.sortBy;

  const cap = document.querySelector('#tcaptcha_transform_dy');
  if (cap && cap.getBoundingClientRect().top >= 0) return { error: 'captcha' };

  const idMap = {
    'relevance': 'FFD', 'date': 'PT',
    'citations': 'CF', 'downloads': 'DFR', 'comprehensive': 'ZH'
  };

  const liId = idMap[sortBy];
  if (!liId) return { error: 'invalid_sort', valid: Object.keys(idMap) };

  const li = document.querySelector('#orderList li#' + liId);
  if (!li) return { error: 'sort_option_not_found' };

  const prevMark = document.querySelector('.countPageMark')?.innerText;
  li.click();

  await new Promise((resolve, reject) => {
    let n = 0;
    const check = () => {
      const mark = document.querySelector('.countPageMark')?.innerText;
      if (mark && mark !== prevMark) resolve();
      else if (++n > 30) reject('timeout');
      else setTimeout(check, 500);
    };
    setTimeout(check, 1000);
  });

  const cap2 = document.querySelector('#tcaptcha_transform_dy');
  if (cap2 && cap2.getBoundingClientRect().top >= 0) return { error: 'captcha' };

  // Extract results from sorted page
  const rows = document.querySelectorAll('.result-table-list tbody tr');
  const checkboxes = document.querySelectorAll('.result-table-list tbody input.cbItem');
  const results = Array.from(rows).map((row, i) => {
    const nameCell = row.querySelector('td.name');
    const titleLink = nameCell?.querySelector('a.fz14');
    const authors = Array.from(row.querySelectorAll('td.author a.KnowledgeNetLink') || [])
      .map(a => a.innerText?.trim()).filter(Boolean);
    const journal = row.querySelector('td.source a')?.innerText?.trim() || '';
    const date = row.querySelector('td.date')?.innerText?.trim() || '';
    const database = row.querySelector('td.data')?.innerText?.trim() || '';
    const citations = row.querySelector('td.quote')?.innerText?.trim() || '';
    const downloads = row.querySelector('td.download')?.innerText?.trim() || '';
    return {
      title: titleLink?.innerText?.trim() || '',
      href: titleLink?.href || '',
      exportId: checkboxes[i]?.value || '',
      authors,
      journal,
      date,
      database,
      citations,
      downloads,
      isOnlineFirst: !!nameCell?.querySelector('.marktip'),
    };
  }).filter(r => r.title);

  return {
    sortBy,
    total: document.querySelector('.pagerTitleCell')?.innerText?.match(/([\\d,]+)/)?.[1] || '0',
    page: document.querySelector('.countPageMark')?.innerText || '?',
    activeLi: document.querySelector('#orderList li.cur')?.innerText?.trim(),
    results,
    url: location.href
  };
}
"""

SortOption = Literal["relevance", "date", "citations", "downloads", "comprehensive"]
PageAction = Literal["next", "previous"]
