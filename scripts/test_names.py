from research_core.rag.rendering import _format_first_author, _format_authors_short

tests = [
    (['James E. Anderson', 'Eric van Wincoop'], 'Anderson', 'Anderson & Wincoop'),
    (['Chen J.', 'Liu M.'], 'Chen', 'Chen & Liu'),
    (['Wang Xiaoming'], 'Wang', 'Wang'),
    (['Smith R. T.', 'Johnson K.'], 'Smith', 'Smith & Johnson'),
    (['Lee H. S.', 'Kim J. W.'], 'Lee', 'Lee & Kim'),
    (['Zhang Y.', 'Patel R.'], 'Zhang', 'Zhang & Patel'),
    (['Miller D. A.'], 'Miller', 'Miller'),
    (['Brown A.', 'Davis C.', 'Wilson E.', 'Taylor M.'], 'Brown', 'Brown et al.'),
    (['Kim Soohyun'], 'Kim', 'Kim'),
    (['van Gogh', 'de Kooning'], 'Gogh', 'Gogh & Kooning'),
    (['Eric van Wincoop'], 'Wincoop', 'Wincoop'),
]

all_ok = True
for authors, exp_first, exp_short in tests:
    got_first = _format_first_author(authors)
    got_short = _format_authors_short(authors)
    first_ok = (got_first == exp_first)
    short_ok = (got_short == exp_short)
    status = "OK" if (first_ok and short_ok) else "FAIL"
    if not first_ok or not short_ok:
        all_ok = False
    print(f'{status}: {authors}')
    print(f'  first={got_first!r} (expect {exp_first!r}), short={got_short!r} (expect {exp_short!r})')

print()
print('ALL TESTS PASSED!' if all_ok else 'SOME TESTS FAILED!')
