import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import config
from chrome_browser import attach_chrome
from jobs import restore_page_load_timeout

d = attach_chrome()
restore_page_load_timeout(d)
d.get(config.LEAGUE_URL + "/standings?sort=AR")
time.sleep(3.5)
info = d.execute_script(
    """
    const selects = [...document.querySelectorAll('select')].map(s => ({
      name: s.name || s.id || '',
      opts: [...s.options].map(o => ({v: o.value, t: (o.textContent || '').trim()}))
    }));
    const tables = [...document.querySelectorAll('table')].map((t, i) => {
      const heads = [...t.querySelectorAll('th')].map(th => (th.textContent || '').trim());
      const rows = [...t.querySelectorAll('tbody tr')].slice(0, 2).map(tr => ({
        cells: [...tr.querySelectorAll('td')].map(td => (td.textContent || '').replace(/\\s+/g, ' ').trim()),
        links: [...tr.querySelectorAll('a[href*=\"/nba/\"]')].slice(0, 3).map(a => ({
          href: a.getAttribute('href'),
          text: (a.textContent || '').trim()
        }))
      }));
      return {i, id: t.id || '', heads, rows};
    });
    return {url: location.href, selects, tables};
    """
)
print(json.dumps(info, indent=2)[:8000])

# Try headtoheadstats
d.get(config.LEAGUE_URL + "/headtoheadstats")
time.sleep(3)
info2 = d.execute_script(
    """
    const selects = [...document.querySelectorAll('select')].map(s => ({
      name: s.name || s.id || '',
      opts: [...s.options].map(o => ({v: o.value, t: (o.textContent || '').trim()})).slice(0, 40)
    }));
    const tables = [...document.querySelectorAll('table')].slice(0, 4).map((t, i) => ({
      i,
      id: t.id || '',
      heads: [...t.querySelectorAll('th')].map(th => (th.textContent || '').trim()).slice(0, 20),
      rows: t.querySelectorAll('tbody tr').length,
      first: [...(t.querySelector('tbody tr')?.querySelectorAll('td') || [])]
        .map(td => (td.textContent || '').replace(/\\s+/g, ' ').trim()).slice(0, 16)
    }));
    return {url: location.href, selects, tables};
    """
)
print("==== H2H STATS ====")
print(json.dumps(info2, indent=2)[:5000])
