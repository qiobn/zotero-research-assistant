"""A/B comparison: Old JSON-only output vs New dual-format output.

Generates both formats from the same mock data and writes them to files
for side-by-side human + LLM comparison.
"""
import json
import os
from dataclasses import dataclass, field

# ── Realistic mock data: 8 papers across mixed quality ──

@dataclass
class MockHit:
    key: str = ''
    title: str = ''
    authors: list = field(default_factory=list)
    year: int = 0
    doi: str = ''
    tags: list = field(default_factory=list)
    score: float = 0.0
    matched_passage: str = ''
    matched_page: int = 0
    source: str = 'hybrid'
    paper_abstract: str = ''
    section_heading: str = ''
    section_type: str = ''
    relevance_tier: str = ''

hits = [
    MockHit(
        key='8JN2K3MA',
        title='Urban Green Spaces and Public Health: A Systematic Review of 156 Studies',
        authors=['Wang Xiaoming', 'Li Yuhang', 'Zhang H.'],
        year=2024, doi='10.1016/j.landurbplan.2024.105012', score=0.0412,
        matched_passage=(
            'This systematic review of 156 studies across 34 countries confirms '
            'a significant positive association between proximity to urban green '
            'spaces and mental health outcomes. The strongest effects were observed '
            'for anxiety reduction (d=0.42) and stress recovery (d=0.38). Physical '
            'activity mediates approximately 35% of the total effect of green space '
            'exposure on mental health. The review also identifies socioeconomic '
            'status as a key moderator, with stronger effects observed in low-income '
            'neighborhoods.'
        ),
        matched_page=12, source='hybrid', relevance_tier='high',
        paper_abstract=(
            'Urban green spaces have been increasingly recognized as important '
            'determinants of public health. This systematic review synthesizes '
            'evidence from 156 studies across 34 countries, examining the pathways '
            'through which green space exposure affects physical and mental health outcomes.'
        ),
        section_heading='3.2 Health Outcomes', section_type='results',
    ),
    MockHit(
        key='7IM1J2LB',
        title='The Relationship Between Park Accessibility and Cardiovascular Health: A Longitudinal Study',
        authors=['Chen J.', 'Liu M.', 'Park S.'],
        year=2023, doi='10.1093/eurpub/ckad089', score=0.0388,
        matched_passage=(
            'Using a longitudinal cohort of 45,000 participants across 18 Chinese '
            'cities, we found that each 10% increase in neighborhood green coverage '
            'was associated with a 4.2% reduction in cardiovascular disease incidence '
            '(95% CI: 2.1-6.3%). The effect was strongest among elderly populations '
            'and those with pre-existing hypertension, suggesting green space '
            'interventions may be particularly cost-effective for these high-risk groups.'
        ),
        matched_page=8, source='hybrid', relevance_tier='high',
        paper_abstract=(
            'Access to urban parks and green spaces may influence cardiovascular health '
            'through multiple pathways including physical activity, stress reduction, '
            'and social cohesion. This longitudinal study examines these relationships '
            'using a cohort of 45,000 participants across 18 Chinese cities.'
        ),
        section_heading='3.1 Main Effects', section_type='results',
    ),
    MockHit(
        key='5FK0H9NA',
        title='Green Infrastructure and Air Quality: Evidence from Satellite Data',
        authors=['Smith R. T.', 'Johnson K.'],
        year=2022, doi='10.1038/s41598-022-12345', score=0.0351,
        matched_passage=(
            'Satellite-derived NDVI data combined with ground-level PM2.5 measurements '
            'from 200 cities reveals that a 0.1 increase in neighborhood NDVI is '
            'associated with a 1.2 microgram/m3 reduction in PM2.5 concentrations. '
            'This effect is mediated primarily through deposition of particulate matter '
            'on leaf surfaces, with coniferous species showing 2.3x greater effectiveness '
            'than deciduous species.'
        ),
        matched_page=5, source='semantic', relevance_tier='medium',
        paper_abstract=(
            'Green infrastructure plays a critical role in urban air quality management. '
            'This study uses satellite-derived vegetation indices and ground-level air '
            'quality monitoring data to quantify the relationship between urban greenery '
            'and particulate matter concentrations.'
        ),
        section_heading='2. Methods', section_type='methods',
    ),
    MockHit(
        key='4DJ3G8MC',
        title='Mental Health Benefits of Urban Greening: A Meta-Analysis',
        authors=['Brown A.', 'Davis C.', 'Wilson E.', 'Taylor M.'],
        year=2025, doi='10.1016/j.socscimed.2025.116789', score=0.0329,
        matched_passage=(
            'Meta-analysis of 47 randomized controlled trials and quasi-experimental '
            'studies demonstrates moderate-to-strong effects of urban greening interventions '
            'on mental health outcomes (g=0.48, 95% CI: 0.35-0.61). Effects were largest '
            'for community gardening programs (g=0.62) and street tree planting (g=0.51), '
            'with smaller effects for park renovations (g=0.31).'
        ),
        matched_page=15, source='hybrid', relevance_tier='medium',
        paper_abstract=(
            'The mental health benefits of urban greening have been widely studied but '
            'effect sizes vary considerably across intervention types and study designs. '
            'This meta-analysis synthesizes 47 experimental and quasi-experimental studies.'
        ),
        section_heading='4.1 Main Analysis', section_type='results',
    ),
    MockHit(
        key='2BH1E7LD',
        title='Economic Valuation of Urban Ecosystem Services: Hedonic Pricing Approach',
        authors=['Lee H. S.', 'Kim J. W.'],
        year=2021, doi='10.1016/j.ecolecon.2021.107234', score=0.0297,
        matched_passage=(
            'Hedonic pricing analysis of 2.3 million property transactions across 15 US '
            'metropolitan areas reveals that properties within 500m of parks command an '
            '8-12% price premium. The premium decays with distance, halving at approximately '
            '1km. When combined with health cost savings from increased physical activity, '
            'the total economic benefit of urban parks is estimated at $4.2 billion annually '
            'across the study areas.'
        ),
        matched_page=22, source='keyword', relevance_tier='low',
        paper_abstract=(
            'Urban green spaces provide ecosystem services that are capitalized into '
            'property values. This study uses hedonic pricing methods to estimate the '
            'economic value of proximity to parks and green spaces.'
        ),
        section_heading='5. Discussion', section_type='discussion',
    ),
    MockHit(
        key='9PK5L4XD',
        title='城市绿地与居民心理健康：基于中国12个城市的实证研究',
        authors=['张伟', '李娜', '王小明'],
        year=2023, doi='10.11821/dlxb202312005', score=0.0315,
        matched_passage=(
            '基于中国12个城市的10,847份有效问卷分析发现：城市绿地可达性与居民心理'
            '健康评分呈显著正相关（r=0.32, p<0.001）。进一步的中介效应分析表明，体力'
            '活动和社会交往分别中介了总效应的28%和22%，两者共同中介了41%的总效应。'
            '研究还发现绿地的质量（植被多样性、维护水平）比单纯的数量（覆盖率）对'
            '心理健康的影响更显著。'
        ),
        matched_page=10, source='hybrid', relevance_tier='high',
        paper_abstract=(
            '快速城市化背景下，城市绿地对居民健康的促进作用日益受到关注。本研究基于'
            '中国12个城市的10,847份问卷调查数据，采用结构方程模型分析绿地可达性对'
            '居民心理健康的影响路径。'
        ),
        section_heading='3. 实证结果', section_type='results',
    ),
    MockHit(
        key='3CE2F6PB',
        title='A Critical Review of Green Space Exposure Assessment Methods',
        authors=['Miller D. A.'],
        year=2020, doi='10.1146/annurev-publhealth-040119-094201', score=0.0271,
        matched_passage=(
            'A fundamental limitation of the existing literature is the heterogeneity of '
            'green space exposure metrics. Studies using NDVI-based measures find larger '
            'effect sizes (g=0.52) compared to those using self-reported proximity (g=0.31) '
            'or land-use classification (g=0.28). This measurement heterogeneity accounts '
            'for approximately 40% of the between-study variance in meta-analyses. Future '
            'studies should prioritize standardized exposure assessment protocols.'
        ),
        matched_page=28, source='semantic', relevance_tier='medium',
        paper_abstract=(
            'Assessment of green space exposure remains a methodological challenge in '
            'environmental health research. This critical review examines the validity '
            'and reliability of commonly used exposure metrics.'
        ),
        section_heading='4. Methodological Gaps', section_type='discussion',
    ),
    MockHit(
        key='1AD0G5NE',
        title='Machine Learning Approaches to Urban Vegetation Mapping',
        authors=['Zhang Y.', 'Patel R.', 'Garcia M.', 'Thompson K. L.'],
        year=2024, doi='10.1016/j.rse.2024.114052', score=0.0255,
        matched_passage=(
            'We present a deep learning framework using Sentinel-2 imagery and LiDAR data '
            'to classify urban vegetation at 1m resolution across 50 cities. Our model '
            'achieves 94.3% accuracy in distinguishing tree canopy from grass and shrub '
            'cover, significantly outperforming traditional NDVI thresholding (78.1%).'
        ),
        matched_page=3, source='keyword', relevance_tier='low',
        paper_abstract=(
            'Accurate mapping of urban vegetation is essential for environmental monitoring '
            'and urban planning. This study develops a deep learning approach for high-'
            'resolution urban vegetation classification.'
        ),
        section_heading='2.1 Model Architecture', section_type='methods',
    ),
]

# ═══════════════════════════════════════════════════════════════
# 生成旧格式 (纯 JSON)
# ═══════════════════════════════════════════════════════════════

old_items = []
for h in hits:
    old_items.append({
        'key': h.key,
        'title': h.title,
        'authors': h.authors,
        'year': h.year,
        'doi': h.doi,
        'tags': h.tags,
        'score': h.score,
        'matched_passage': h.matched_passage,
        'matched_page': h.matched_page,
        'source': h.source,
        'paper_abstract': h.paper_abstract,
        'section_heading': h.section_heading,
        'section_type': h.section_type,
    })
old_output = json.dumps({'data': old_items, 'count': len(old_items)}, ensure_ascii=False, indent=2)

# ═══════════════════════════════════════════════════════════════
# 生成新格式 (JSON items + context_block)
# ═══════════════════════════════════════════════════════════════

from research_core.rag.rendering import get_renderer
renderer = get_renderer()
context_block = renderer.render_search_results('urban green space health effects', hits, limit=10)

new_items = []
for h in hits:
    new_items.append({
        'key': h.key,
        'title': h.title,
        'authors': h.authors,
        'year': h.year,
        'doi': h.doi,
        'tags': h.tags,
        'score': h.score,
        'matched_page': h.matched_page,
        'source': h.source,
        'relevance_tier': h.relevance_tier,
        'section_heading': h.section_heading,
        'section_type': h.section_type,
    })
new_output = json.dumps({
    'count': len(hits),
    'query': 'urban green space health effects',
    'items': new_items,
    'context_block': context_block,
}, ensure_ascii=False, indent=2)

# ═══════════════════════════════════════════════════════════════
# Token 统计
# ═══════════════════════════════════════════════════════════════

try:
    import tiktoken
    enc = tiktoken.get_encoding('cl100k_base')
    old_tokens = len(enc.encode(old_output))
    new_tokens = len(enc.encode(new_output))
    cb_tokens = len(enc.encode(context_block))
    items_tokens = len(enc.encode(json.dumps(new_items, ensure_ascii=False)))
    print(f'=== TOKEN COUNTS ===')
    print(f'Old format (pure JSON):        {old_tokens:>5,} tokens')
    print(f'New format items only:         {items_tokens:>5,} tokens')
    print(f'New format context_block only: {cb_tokens:>5,} tokens')
    print(f'New format total (items+CB):   {new_tokens:>5,} tokens')
    print(f'Delta: {new_tokens - old_tokens:+,} tokens ({(new_tokens/old_tokens - 1)*100:+.0f}%)')
except ImportError:
    print('tiktoken not available — skipping token counts')

# ═══════════════════════════════════════════════════════════════
# 写出文件供对比
# ═══════════════════════════════════════════════════════════════

out_dir = os.path.join(os.path.dirname(__file__), '..', 'scripts')
os.makedirs(out_dir, exist_ok=True)

with open(os.path.join(out_dir, 'output_old.json'), 'w', encoding='utf-8') as f:
    f.write(old_output)

with open(os.path.join(out_dir, 'output_new.json'), 'w', encoding='utf-8') as f:
    f.write(new_output)

with open(os.path.join(out_dir, 'output_context_block.md'), 'w', encoding='utf-8') as f:
    f.write(context_block)

print(f'Files written to scripts/output_old.json, output_new.json, output_context_block.md')

# ═══════════════════════════════════════════════════════════════
# Print both for direct comparison
# ═══════════════════════════════════════════════════════════════

print('\n' + '='*70)
print('=== OLD FORMAT (Pure JSON) ===')
print('='*70)
print(old_output[:3000])
if len(old_output) > 3000:
    print(f'\n... ({len(old_output) - 3000} more chars) ...')

print('\n' + '='*70)
print('=== NEW FORMAT — Context Block (Markdown) ===')
print('='*70)
print(context_block)

print('\n' + '='*70)
print('=== NEW FORMAT — Items (JSON metadata) ===')
print('='*70)
print(json.dumps(new_items[:3], ensure_ascii=False, indent=2))
if len(new_items) > 3:
    print(f'\n... ({len(new_items) - 3} more items omitted) ...')
