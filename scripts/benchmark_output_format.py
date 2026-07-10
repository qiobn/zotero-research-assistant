"""Token efficiency comparison: old JSON-only vs new dual-format output."""
import json
from dataclasses import dataclass

# Build realistic mock search results
@dataclass
class MockHit:
    key: str = ''; title: str = ''; authors: list = None; year: int = 0
    doi: str = ''; tags: list = None; score: float = 0.0
    matched_passage: str = ''; matched_page: int = 0; source: str = 'hybrid'
    paper_abstract: str = ''; section_heading: str = ''; section_type: str = ''
    relevance_tier: str = ''

hits = [
    MockHit(key='8JN2K3MA', title='Urban Green Spaces and Public Health: A Systematic Review of 156 Studies',
            authors=['Wang Xiaoming', 'Li Yuhang', 'Zhang H.'],
            year=2024, doi='10.1016/j.landurbplan.2024.105012', score=0.0412,
            matched_passage='This systematic review of 156 studies across 34 countries confirms a significant positive association between proximity to urban green spaces and mental health outcomes. The strongest effects were observed for anxiety reduction (d=0.42) and stress recovery (d=0.38). Physical activity mediates approximately 35% of the total effect of green space exposure on mental health. The review also identifies socioeconomic status as a key moderator, with stronger effects observed in low-income neighborhoods.',
            matched_page=12, source='hybrid', relevance_tier='high',
            paper_abstract='Urban green spaces have been increasingly recognized as important determinants of public health. This systematic review synthesizes evidence from 156 studies across 34 countries...',
            section_heading='3.2 Health Outcomes', section_type='results'),
    MockHit(key='7IM1J2LB', title='The Relationship Between Park Accessibility and Cardiovascular Health: A Longitudinal Study',
            authors=['Chen J.', 'Liu M.', 'Park S.'],
            year=2023, doi='10.1093/eurpub/ckad089', score=0.0388,
            matched_passage='Using a longitudinal cohort of 45,000 participants across 18 Chinese cities, we found that each 10% increase in neighborhood green coverage was associated with a 4.2% reduction in cardiovascular disease incidence (95% CI: 2.1-6.3%). The effect was strongest among elderly populations and those with pre-existing hypertension, suggesting green space interventions may be particularly cost-effective for these high-risk groups.',
            matched_page=8, source='hybrid', relevance_tier='high',
            paper_abstract='Access to urban parks and green spaces may influence cardiovascular health through multiple pathways including physical activity...',
            section_heading='3.1 Main Effects', section_type='results'),
    MockHit(key='5FK0H9NA', title='Green Infrastructure and Air Quality: Evidence from Satellite Data',
            authors=['Smith R. T.', 'Johnson K.'],
            year=2022, doi='10.1038/s41598-022-12345', score=0.0351,
            matched_passage='Satellite-derived NDVI data combined with ground-level PM2.5 measurements from 200 cities reveals that a 0.1 increase in neighborhood NDVI is associated with a 1.2 microgram/m3 reduction in PM2.5 concentrations. This effect is mediated primarily through deposition of particulate matter on leaf surfaces, with coniferous species showing 2.3x greater effectiveness than deciduous species.',
            matched_page=5, source='semantic', relevance_tier='medium',
            paper_abstract='Green infrastructure plays a critical role in urban air quality management...',
            section_heading='2. Methods', section_type='methods'),
    MockHit(key='4DJ3G8MC', title='Mental Health Benefits of Urban Greening: A Meta-Analysis',
            authors=['Brown A.', 'Davis C.', 'Wilson E.', 'Taylor M.'],
            year=2025, doi='10.1016/j.socscimed.2025.116789', score=0.0329,
            matched_passage='Meta-analysis of 47 randomized controlled trials and quasi-experimental studies demonstrates moderate-to-strong effects of urban greening interventions on mental health outcomes (g=0.48, 95% CI: 0.35-0.61). Effects were largest for community gardening programs (g=0.62) and street tree planting (g=0.51), with smaller effects for park renovations (g=0.31).',
            matched_page=15, source='hybrid', relevance_tier='medium',
            paper_abstract='The mental health benefits of urban greening have been widely studied but effect sizes vary considerably across intervention types...',
            section_heading='4.1 Main Analysis', section_type='results'),
    MockHit(key='2BH1E7LD', title='Economic Valuation of Urban Ecosystem Services: Hedonic Pricing Approach',
            authors=['Lee H. S.', 'Kim J. W.'],
            year=2021, doi='10.1016/j.ecolecon.2021.107234', score=0.0297,
            matched_passage='Hedonic pricing analysis of 2.3 million property transactions across 15 US metropolitan areas reveals that properties within 500m of parks command a 8-12% price premium. The premium decays with distance, halving at approximately 1km. When combined with health cost savings from increased physical activity, the total economic benefit of urban parks is estimated at $4.2 billion annually across the study areas.',
            matched_page=22, source='keyword', relevance_tier='low',
            paper_abstract='Urban green spaces provide ecosystem services that are capitalized into property values...',
            section_heading='5. Discussion', section_type='discussion'),
    # CJK paper
    MockHit(key='9PK5L4XD', title='城市绿地与居民心理健康：基于中国12个城市的实证研究',
            authors=['张伟', '李娜', '王小明'],
            year=2023, doi='10.11821/dlxb202312005', score=0.0315,
            matched_passage='基于中国12个城市的10,847份有效问卷分析发现：城市绿地可达性与居民心理健康评分呈显著正相关（r=0.32, p<0.001）。进一步的中介效应分析表明，体力活动和社会交往分别中介了总效应的28%和22%，两者共同中介了41%的总效应。研究还发现绿地的质量（植被多样性、维护水平）比单纯的数量（覆盖率）对心理健康的影响更显著。',
            matched_page=10, source='hybrid', relevance_tier='high',
            paper_abstract='快速城市化背景下，城市绿地对居民健康的促进作用日益受到关注。本研究基于中国12个城市的问卷调查数据...',
            section_heading='3. 实证结果', section_type='results'),
]

# === OLD FORMAT (pure JSON) ===
old_items = []
for h in hits:
    old_items.append({
        'key': h.key, 'title': h.title, 'authors': h.authors,
        'year': h.year, 'doi': h.doi, 'tags': h.tags or [],
        'score': h.score, 'matched_passage': h.matched_passage,
        'matched_page': h.matched_page, 'source': h.source,
        'paper_abstract': h.paper_abstract,
        'section_heading': h.section_heading,
        'section_type': h.section_type,
    })
old_output = json.dumps({'data': old_items, 'count': len(old_items)}, ensure_ascii=False)

# === NEW FORMAT (JSON items + context_block) ===
from research_core.rag.rendering import get_renderer
renderer = get_renderer()
context_block = renderer.render_search_results('urban green space health', hits, limit=10)

new_items = []
for h in hits:
    new_items.append({
        'key': h.key, 'title': h.title, 'authors': h.authors,
        'year': h.year, 'doi': h.doi, 'tags': h.tags or [],
        'score': h.score, 'matched_page': h.matched_page,
        'source': h.source, 'relevance_tier': h.relevance_tier,
        'section_heading': h.section_heading,
        'section_type': h.section_type,
    })
new_output = json.dumps({
    'count': len(hits), 'query': 'urban green space health',
    'items': new_items, 'context_block': context_block,
}, ensure_ascii=False)

print(f'=== RAW CHARACTER COUNTS ===')
print(f'Old format (pure JSON):  {len(old_output):,} chars')
print(f'New format (JSON+MD):    {len(new_output):,} chars')
print(f'New context_block only:  {len(context_block):,} chars')
print()

# Use tiktoken if available
try:
    import tiktoken
    enc = tiktoken.get_encoding('cl100k_base')
    old_tokens = len(enc.encode(old_output))
    new_tokens = len(enc.encode(new_output))
    cb_tokens = len(enc.encode(context_block))
    print(f'=== TOKEN COUNTS (cl100k_base) ===')
    print(f'Old format (pure JSON):  {old_tokens:,} tokens')
    print(f'New format (JSON+MD):    {new_tokens:,} tokens')
    print(f'New context_block only:  {cb_tokens:,} tokens')
    print()
    print(f'=== EFFICIENCY ===')
    print(f'Total savings: {old_tokens - new_tokens:,} tokens ({(1 - new_tokens/old_tokens)*100:.0f}%)')
    print(f'Context block alone vs old JSON: {(1 - cb_tokens/old_tokens)*100:.0f}% fewer tokens')
    print(f'(Context block carries all evidence the LLM needs)')

except ImportError:
    print('tiktoken not available, using character ratio:')
    print(f'Char savings: {(1 - len(new_output)/len(old_output))*100:.0f}%')

# === CJK NAME VERIFICATION ===
print()
print(f'=== CJK NAME TEST ===')
from research_core.rag.rendering import _format_first_author, _format_authors_short
zh_name = ['张伟', '李娜', '王小明']
en_name = ['James E. Anderson', 'Eric van Wincoop']
print(f'CJK: {zh_name} -> {_format_first_author(zh_name)} (expect: 张)')
print(f'CJK short: {_format_authors_short(zh_name)} (expect: 张 et al.)')
print(f'EN: {en_name} -> {_format_first_author(en_name)} (expect: Anderson)')
print(f'EN short: {_format_authors_short(en_name)} (expect: Anderson & Wincoop)')
