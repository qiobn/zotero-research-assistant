"""CNKI paper detail extraction via browser automation.

Navigates to a paper's detail page and extracts full metadata including
DOI, abstract, keywords, affiliations, and citation network.
Adapted from cookjohn/cnki-skills cnki-paper-detail.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PAPER_DETAIL_JS = """
async (params) => {
  const url = params.url;

  if (url) {
    window.location.href = url;
    await new Promise((resolve, reject) => {
      let n = 0;
      const check = () => {
        if (document.querySelector('.brief') || document.querySelector('.abstract-text')) resolve();
        else if (++n > 40) reject('timeout');
        else setTimeout(check, 500);
      };
      setTimeout(check, 1000);
    });
  }

  const cap = document.querySelector('#tcaptcha_transform_dy');
  if (cap && cap.getBoundingClientRect().top >= 0) return { error: 'captcha' };

  const brief = document.querySelector('.brief');
  if (!brief) return { error: 'not_detail_page' };

  const title = (brief.querySelector('h1')?.innerText?.trim() || '')
    .replace(/\\s*附视频\\s*$/, '')
    .replace(/\\s*网络首发\\s*$/, '');

  const authorH3s = brief.querySelectorAll('h3.author');
  const authorSection = authorH3s[0];
  const authors = [];
  if (authorSection) {
    const authorLinks = authorSection.querySelectorAll('a');
    authorLinks.forEach(a => {
      const name = a.innerText?.replace(/\\d+$/, '').trim();
      if (name) authors.push(name);
    });
  }

  const affiliations = [];
  if (authorH3s.length > 1) {
    const orgLinks = authorH3s[1].querySelectorAll('a');
    orgLinks.forEach(a => {
      const t = a.innerText?.trim();
      if (t) affiliations.push(t);
    });
  }

  const abstractEl = document.querySelector('.abstract-text');
  const abstract = abstractEl?.innerText?.trim() || '';

  const keywordsP = document.querySelector('p.keywords');
  const keywords = keywordsP
    ? Array.from(keywordsP.querySelectorAll('a')).map(a => a.innerText?.replace(/;$/, '').trim()).filter(Boolean)
    : [];

  const fundsP = document.querySelector('p.funds');
  const fund = fundsP?.innerText?.trim() || '';

  const docTop = document.querySelector('.doc-top');
  const journal = docTop?.querySelector('a')?.innerText?.trim() || '';

  const headTime = document.querySelector('.head-time');
  const pubInfo = headTime?.innerText?.trim() || '';

  // Extract DOI from page body
  const bodyText = document.body.innerText;
  const doiMatch = bodyText.match(/DOI[：:]\\s*(10\\.[^\\s]+)/i);
  const doi = doiMatch ? doiMatch[1].replace(/[,;。]$/, '') : '';

  // Export ID for Zotero integration
  const exportId = document.querySelector('#export-id')?.value || '';
  const exportUrl = document.querySelector('#export-url')?.value || '';

  // ISSN
  const issnMatch = bodyText.match(/ISSN[：:]\\s*(\\S+)/);
  const issn = issnMatch ? issnMatch[1] : '';

  // Citation count
  const citationTabs = document.querySelectorAll('ul.module-tab.tpl_lieteratures li');
  const citationInfo = {};
  citationTabs.forEach(li => {
    const id = li.getAttribute('data-id');
    const text = li.innerText?.trim();
    const countMatch = text.match(/(\\d+)/);
    if (id) {
      citationInfo[id] = {
        label: text.replace(/\\d+/, '').trim(),
        count: countMatch ? parseInt(countMatch[1]) : 0
      };
    }
  });

  return {
    title,
    authors,
    affiliations,
    abstract,
    keywords,
    fund,
    journal,
    pubInfo,
    doi,
    issn,
    exportId,
    exportUrl,
    citationInfo,
    pageUrl: window.location.href
  };
}
"""


@dataclass
class CnkiPaperDetail:
    """Full metadata extracted from a CNKI paper detail page."""

    title: str = ""
    authors: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)
    abstract: str = ""
    keywords: list[str] = field(default_factory=list)
    fund: str = ""
    journal: str = ""
    pub_info: str = ""
    doi: str = ""
    issn: str = ""
    export_id: str = ""
    citation_info: dict = field(default_factory=dict)
    page_url: str = ""


def extract_paper_detail(page, cnki_url: str = "") -> CnkiPaperDetail:
    """Extract full metadata from a CNKI paper detail page.

    Args:
        page: Playwright page instance (already on CNKI).
        cnki_url: URL of the paper detail page to navigate to.
                  If empty, assumes the page is already on a detail page.
    """
    raw = page.evaluate(PAPER_DETAIL_JS, {"url": cnki_url})

    if raw.get("error") == "captcha":
        from research_core.sources.cnki.exceptions import CnkiCaptchaError
        raise CnkiCaptchaError("CNKI captcha detected on detail page.")
    if raw.get("error") == "not_detail_page":
        return CnkiPaperDetail()

    return CnkiPaperDetail(
        title=raw.get("title", ""),
        authors=raw.get("authors", []),
        affiliations=raw.get("affiliations", []),
        abstract=raw.get("abstract", ""),
        keywords=raw.get("keywords", []),
        fund=raw.get("fund", ""),
        journal=raw.get("journal", ""),
        pub_info=raw.get("pubInfo", ""),
        doi=raw.get("doi", ""),
        issn=raw.get("issn", ""),
        export_id=raw.get("exportId", ""),
        citation_info=raw.get("citationInfo", {}),
        page_url=raw.get("pageUrl", ""),
    )
