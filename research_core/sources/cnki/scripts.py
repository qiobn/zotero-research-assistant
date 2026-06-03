"""Browser-side JavaScript adapted from cookjohn/cnki-skills.

Playwright page.evaluate() requires a single async function expression.
"""

# Playwright passes the Python arg as the first parameter to this function.
BASIC_SEARCH_JS = """
async (params) => {
  const query = params.query;

  function extractCnkiResults(q) {
    const rows = document.querySelectorAll('.result-table-list tbody tr');
    const checkboxes = document.querySelectorAll('.result-table-list tbody input.cbItem');
    const results = Array.from(rows).map((row, i) => {
      const nameCell = row.querySelector('td.name');
      const titleLink = nameCell?.querySelector('a.fz14');
      const authors = Array.from(row.querySelectorAll('td.author a.KnowledgeNetLink') || [])
        .map(a => a.innerText?.trim()).filter(Boolean);
      const sourceCell = row.querySelector('td.source');
      const journal = sourceCell?.querySelector('a')?.innerText?.trim() || '';
      const journalLevel = [];
      if (sourceCell) {
        const badges = sourceCell.querySelectorAll('.icon-hx, .icon-he, [class*=badge], [class*=core]');
        badges.forEach(el => {
          const t = el.innerText?.trim() || el.getAttribute('title') || '';
          if (t) journalLevel.push(t);
        });
        const cellText = sourceCell.innerText || '';
        if (cellText.includes('北大核心') && !journalLevel.includes('北大核心')) journalLevel.push('北大核心');
        if (cellText.includes('CSSCI') && !journalLevel.includes('CSSCI')) journalLevel.push('CSSCI');
        if (cellText.includes('CSCD') && !journalLevel.includes('CSCD')) journalLevel.push('CSCD');
        if (cellText.includes('SCI') && !journalLevel.includes('SCI')) journalLevel.push('SCI');
        if (cellText.includes('EI') && !journalLevel.includes('EI')) journalLevel.push('EI');
        if (cellText.includes('核心') && !journalLevel.includes('北大核心') && !journalLevel.some(l => l.includes('核心'))) journalLevel.push('核心期刊');
      }
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
        journalLevel,
        date,
        database,
        citations,
        downloads,
        isOnlineFirst: !!nameCell?.querySelector('.marktip'),
      };
    }).filter(r => r.title);

    const totalText = document.querySelector('.pagerTitleCell')?.innerText || '';
    const totalMatch = totalText.match(/([\\d,]+)/);

    return {
      query: q,
      total: totalMatch ? totalMatch[1].replace(/,/g, '') : '0',
      page: document.querySelector('.countPageMark')?.innerText || '1/1',
      results,
    };
  }

  await new Promise((r, j) => {
    let n = 0;
    const c = () => {
      if (document.querySelector('input.search-input')) r();
      else if (++n > 20) j('timeout: search input not found');
      else setTimeout(c, 500);
    };
    c();
  });

  const outer = document.querySelector('#tcaptcha_transform_dy');
  if (outer && outer.getBoundingClientRect().top >= 0) return { error: 'captcha' };

  const input = document.querySelector('input.search-input');
  input.value = query;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  document.querySelector('input.search-btn')?.click();

  await new Promise((r, j) => {
    let n = 0;
    const c = () => {
      if (document.body.innerText.includes('条结果')) r();
      else if (++n > 20) j('timeout: results not loaded');
      else setTimeout(c, 500);
    };
    setTimeout(c, 1000);
  });

  const outer2 = document.querySelector('#tcaptcha_transform_dy');
  if (outer2 && outer2.getBoundingClientRect().top >= 0) return { error: 'captcha' };

  return extractCnkiResults(query);
}
"""

ADVANCED_SEARCH_JS = """
async (cfg) => {
  const query = cfg.query || '';
  const fieldType = cfg.fieldType || 'SU';
  const startYear = cfg.startYear || '';
  const endYear = cfg.endYear || '';
  const author = cfg.author || '';
  const journal = cfg.journal || '';
  const sourceTypes = cfg.sourceTypes || [];

  function extractCnkiResults(q) {
    const rows = document.querySelectorAll('.result-table-list tbody tr');
    const checkboxes = document.querySelectorAll('.result-table-list tbody input.cbItem');
    const results = Array.from(rows).map((row, i) => {
      const nameCell = row.querySelector('td.name');
      const titleLink = nameCell?.querySelector('a.fz14');
      const authors = Array.from(row.querySelectorAll('td.author a.KnowledgeNetLink') || [])
        .map(a => a.innerText?.trim()).filter(Boolean);
      const sourceCell = row.querySelector('td.source');
      const journal = sourceCell?.querySelector('a')?.innerText?.trim() || '';
      const journalLevel = [];
      if (sourceCell) {
        const badges = sourceCell.querySelectorAll('.icon-hx, .icon-he, [class*=badge], [class*=core]');
        badges.forEach(el => {
          const t = el.innerText?.trim() || el.getAttribute('title') || '';
          if (t) journalLevel.push(t);
        });
        const cellText = sourceCell.innerText || '';
        if (cellText.includes('北大核心') && !journalLevel.includes('北大核心')) journalLevel.push('北大核心');
        if (cellText.includes('CSSCI') && !journalLevel.includes('CSSCI')) journalLevel.push('CSSCI');
        if (cellText.includes('CSCD') && !journalLevel.includes('CSCD')) journalLevel.push('CSCD');
        if (cellText.includes('SCI') && !journalLevel.includes('SCI')) journalLevel.push('SCI');
        if (cellText.includes('EI') && !journalLevel.includes('EI')) journalLevel.push('EI');
        if (cellText.includes('核心') && !journalLevel.includes('北大核心') && !journalLevel.some(l => l.includes('核心'))) journalLevel.push('核心期刊');
      }
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
        journalLevel,
        date,
        database,
        citations,
        downloads,
        isOnlineFirst: !!nameCell?.querySelector('.marktip'),
      };
    }).filter(r => r.title);

    const totalText = document.querySelector('.pagerTitleCell')?.innerText || '';
    const totalMatch = totalText.match(/([\\d,]+)/);

    return {
      query: q,
      total: totalMatch ? totalMatch[1].replace(/,/g, '') : '0',
      page: document.querySelector('.countPageMark')?.innerText || '1/1',
      results,
    };
  }

  await new Promise((r, j) => {
    let n = 0;
    const c = () => {
      if (document.querySelector('#txt_1_value1')) r();
      else if (n++ > 20) j('timeout: advanced form not found');
      else setTimeout(c, 500);
    };
    c();
  });

  const cap = document.querySelector('#tcaptcha_transform_dy');
  if (cap && cap.getBoundingClientRect().top >= 0) return { error: 'captcha' };

  const selects = Array.from(document.querySelectorAll('select')).filter(s => s.offsetParent !== null);

  if (sourceTypes.length > 0) {
    const gjAll = document.querySelector('#gjAll');
    if (gjAll && gjAll.checked) gjAll.click();
    for (const st of sourceTypes) {
      const cb = document.querySelector('#' + st);
      if (cb && !cb.checked) cb.click();
    }
  }

  if (selects[0]) {
    selects[0].value = fieldType;
    selects[0].dispatchEvent(new Event('change', { bubbles: true }));
  }
  const input = document.querySelector('#txt_1_value1');
  if (input) {
    input.value = query;
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }

  if (author) {
    const auInput = document.querySelector('#au_1_value1');
    if (auInput) {
      auInput.value = author;
      auInput.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  if (journal) {
    const magInput = document.querySelector('#magazine_value1');
    if (magInput) {
      magInput.value = journal;
      magInput.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  if (startYear && selects[14]) {
    selects[14].value = startYear;
    selects[14].dispatchEvent(new Event('change', { bubbles: true }));
  }
  if (endYear && selects[15]) {
    selects[15].value = endYear;
    selects[15].dispatchEvent(new Event('change', { bubbles: true }));
  }

  document.querySelector('div.search')?.click();

  await new Promise((r, j) => {
    let n = 0;
    const c = () => {
      if (document.body.innerText.includes('条结果')) r();
      else if (n++ > 20) j('timeout: adv results not loaded');
      else setTimeout(c, 500);
    };
    setTimeout(c, 1500);
  });

  const cap2 = document.querySelector('#tcaptcha_transform_dy');
  if (cap2 && cap2.getBoundingClientRect().top >= 0) return { error: 'captcha' };

  return extractCnkiResults(query);
}
"""

# Map user-facing source labels to CNKI checkbox IDs (old AdvSearch interface).
SOURCE_CATEGORY_IDS: dict[str, str] = {
    "SCI": "SCI",
    "EI": "EI",
    "北大核心": "hx",
    "核心期刊": "hx",
    "CSSCI": "CSSCI",
    "CSCD": "CSCD",
    "hx": "hx",
}

SEARCH_FIELD_IDS: dict[str, str] = {
    "SU": "SU",
    "TI": "TI",
    "KY": "KY",
    "TKA": "TKA",
    "AB": "AB",
    "主题": "SU",
    "篇名": "TI",
    "关键词": "KY",
    "摘要": "AB",
}

BASIC_SEARCH_URL = "https://kns.cnki.net/kns8s/search"
ADVANCED_SEARCH_URL = "https://kns.cnki.net/kns/AdvSearch?classid=7NS01R8M"
