(() => {
  /** ESPN CDN logos — reliable static PNGs by NBA abbreviation. */
  const TEAMS = [
    { abbr: "ATL", city: "Atlanta", name: "Atlanta Hawks", aliases: ["atl", "hawks", "atlanta hawks"] },
    { abbr: "BOS", city: "Boston", name: "Boston Celtics", aliases: ["bos", "celtics", "boston celtics"] },
    { abbr: "BKN", city: "Brooklyn", name: "Brooklyn Nets", aliases: ["bkn", "brk", "nets", "brooklyn nets"] },
    { abbr: "CHA", city: "Charlotte", name: "Charlotte Hornets", aliases: ["cha", "cho", "hornets", "charlotte hornets"] },
    { abbr: "CHI", city: "Chicago", name: "Chicago Bulls", aliases: ["chi", "bulls", "chicago bulls"] },
    { abbr: "CLE", city: "Cleveland", name: "Cleveland Cavaliers", aliases: ["cle", "cavaliers", "cavs", "cleveland cavaliers"] },
    { abbr: "DAL", city: "Dallas", name: "Dallas Mavericks", aliases: ["dal", "mavericks", "mavs", "dallas mavericks"] },
    { abbr: "DEN", city: "Denver", name: "Denver Nuggets", aliases: ["den", "nuggets", "denver nuggets"] },
    { abbr: "DET", city: "Detroit", name: "Detroit Pistons", aliases: ["det", "pistons", "detroit pistons"] },
    { abbr: "GSW", city: "Golden St.", name: "Golden State Warriors", aliases: ["gsw", "gs", "warriors", "golden state", "golden state warriors", "golden st."] },
    { abbr: "HOU", city: "Houston", name: "Houston Rockets", aliases: ["hou", "rockets", "houston rockets"] },
    { abbr: "IND", city: "Indiana", name: "Indiana Pacers", aliases: ["ind", "pacers", "indiana pacers"] },
    { abbr: "LAC", city: "L.A. Clippers", name: "LA Clippers", aliases: ["lac", "clippers", "la clippers", "l.a. clippers", "los angeles clippers"] },
    { abbr: "LAL", city: "L.A. Lakers", name: "Los Angeles Lakers", aliases: ["lal", "lakers", "la lakers", "l.a. lakers", "los angeles lakers"] },
    { abbr: "MEM", city: "Memphis", name: "Memphis Grizzlies", aliases: ["mem", "grizzlies", "memphis grizzlies"] },
    { abbr: "MIA", city: "Miami", name: "Miami Heat", aliases: ["mia", "heat", "miami heat"] },
    { abbr: "MIL", city: "Milwaukee", name: "Milwaukee Bucks", aliases: ["mil", "bucks", "milwaukee bucks"] },
    { abbr: "MIN", city: "Minnesota", name: "Minnesota Timberwolves", aliases: ["min", "timberwolves", "wolves", "minnesota timberwolves"] },
    { abbr: "NOP", city: "New Orleans", name: "New Orleans Pelicans", aliases: ["nop", "no", "pelicans", "new orleans pelicans"] },
    { abbr: "NYK", city: "New York", name: "New York Knicks", aliases: ["nyk", "ny", "knicks", "new york knicks"] },
    { abbr: "OKC", city: "Oklahoma City", name: "Oklahoma City Thunder", aliases: ["okc", "thunder", "oklahoma city thunder"] },
    { abbr: "ORL", city: "Orlando", name: "Orlando Magic", aliases: ["orl", "magic", "orlando magic"] },
    { abbr: "PHI", city: "Philadelphia", name: "Philadelphia 76ers", aliases: ["phi", "sixers", "76ers", "philadelphia 76ers"] },
    { abbr: "PHX", city: "Phoenix", name: "Phoenix Suns", aliases: ["phx", "pho", "suns", "phoenix suns"] },
    { abbr: "POR", city: "Portland", name: "Portland Trail Blazers", aliases: ["por", "blazers", "trail blazers", "portland trail blazers"] },
    { abbr: "SAC", city: "Sacramento", name: "Sacramento Kings", aliases: ["sac", "kings", "sacramento kings"] },
    { abbr: "SAS", city: "San Antonio", name: "San Antonio Spurs", aliases: ["sas", "sa", "spurs", "san antonio spurs"] },
    { abbr: "TOR", city: "Toronto", name: "Toronto Raptors", aliases: ["tor", "raptors", "toronto raptors"] },
    { abbr: "UTA", city: "Utah", name: "Utah Jazz", aliases: ["uta", "utah", "jazz", "utah jazz"] },
    { abbr: "WAS", city: "Washington", name: "Washington Wizards", aliases: ["was", "wsh", "wizards", "washington wizards"] },
  ];

  function norm(s) {
    return String(s || "")
      .toLowerCase()
      .replace(/\./g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function logoUrl(abbr) {
    return `https://a.espncdn.com/i/teamlogos/nba/500/${String(abbr || "").toLowerCase()}.png`;
  }

  const byKey = new Map();
  for (const t of TEAMS) {
    const entry = { ...t, logo: logoUrl(t.abbr) };
    byKey.set(norm(t.abbr), entry);
    byKey.set(norm(t.city), entry);
    byKey.set(norm(t.name), entry);
    for (const a of t.aliases || []) byKey.set(norm(a), entry);
  }

  function resolve(team) {
    const key = norm(team);
    if (!key || key === "—" || key === "-") return null;
    return byKey.get(key) || null;
  }

  function logoImg(team, { size = 36, className = "nba-team-logo" } = {}) {
    const info = resolve(team);
    if (!info) return "";
    const px = Number(size) || 36;
    return `<img class="${className}" src="${info.logo}" alt="" width="${px}" height="${px}" loading="lazy" decoding="async">`;
  }

  window.NbaTeams = { TEAMS, resolve, logoUrl, logoImg };
})();
