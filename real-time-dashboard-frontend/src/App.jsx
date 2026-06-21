import React, { useState, useEffect, useCallback } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, BarChart, Bar } from 'recharts';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000/api';

const POLL_INTERVAL = 15000;

const ACTIVITY_ICONS = { commit: '🛠', pr: '🔀', issue: '📋' };
const ACTIVITY_COLORS = { commit: '#6366f1', pr: '#10b981', issue: '#f59e0b' };

function useApi(endpoint, poll = true) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetcher = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}${endpoint}`);
      if (!res.ok) throw new Error(`${res.status}`);
      const json = await res.json();
      setData(json);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [endpoint]);

  useEffect(() => {
    fetcher();
    if (!poll) return;
    const id = setInterval(fetcher, POLL_INTERVAL);
    return () => clearInterval(id);
  }, [fetcher, poll]);

  return { data, loading, error, refetch: fetcher };
}

function SearchBar({ value, onChange, typeFilter, onTypeChange }) {
  return (
    <div style={styles.searchBar}>
      <input
        style={styles.searchInput}
        placeholder="Search by author, repo, or message..."
        value={value}
        onChange={e => onChange(e.target.value)}
      />
      <div style={styles.filterButtons}>
        {['all', 'commit', 'pr', 'issue'].map(t => (
          <button
            key={t}
            onClick={() => onTypeChange(t)}
            style={{
              ...styles.filterBtn,
              ...(typeFilter === t ? styles.filterBtnActive : {})
            }}
          >
            {t === 'all' ? 'All' : t === 'pr' ? 'PRs' : t.charAt(0).toUpperCase() + t.slice(1) + 's'}
          </button>
        ))}
      </div>
    </div>
  );
}

function ActivityFeed({ activities, search, typeFilter }) {
  const filtered = (activities || []).filter(a => {
    const matchType = typeFilter === 'all' || a.type === typeFilter;
    const q = search.toLowerCase();
    const matchSearch = !q ||
      (a.author || '').toLowerCase().includes(q) ||
      (a.repo || '').toLowerCase().includes(q) ||
      (a.message || '').toLowerCase().includes(q);
    return matchType && matchSearch;
  });

  return (
    <div style={styles.feed}>
      <h3 style={styles.sectionTitle}>Live Activity</h3>
      {filtered.length === 0 && <p style={styles.empty}>No activity found.</p>}
      {filtered.slice(0, 50).map((a, i) => (
        <div key={a.id || i} style={{ ...styles.feedItem, borderLeftColor: ACTIVITY_COLORS[a.type] || '#888' }}>
          <div style={styles.feedHeader}>
            <span style={styles.feedIcon}>{ACTIVITY_ICONS[a.type] || '•'}</span>
            <strong style={styles.feedAuthor}>{a.author}</strong>
            <span style={styles.feedType}>({a.type})</span>
            <span style={styles.feedTime}>{new Date(a.timestamp).toLocaleTimeString()}</span>
          </div>
          <div style={styles.feedBody}>
            <span style={styles.feedRepo}>{a.repo}</span>
            <span style={styles.feedMsg}>{a.message}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function Leaderboard({ contributors }) {
  const sorted = [...(contributors || [])].sort((a, b) => b.score - a.score).slice(0, 10);
  return (
    <div style={styles.leaderboard}>
      <h3 style={styles.sectionTitle}>Top Contributors</h3>
      {sorted.map((c, i) => (
        <div key={c.author || i} style={styles.leaderRow}>
          <span style={styles.leaderRank}>#{i + 1}</span>
          <img src={c.avatar || `https://github.com/${c.author}.png?size=32`} alt="" style={styles.avatar} />
          <span style={styles.leaderName}>{c.author}</span>
          <span style={styles.leaderScore}>{c.score}</span>
        </div>
      ))}
    </div>
  );
}

function BurndownChart({ data }) {
  if (!data || data.length === 0) return <div style={styles.chart}><p>No burndown data.</p></div>;
  return (
    <div style={styles.chart}>
      <h3 style={styles.sectionTitle}>Sprint Burndown</h3>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={data} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#2d3748" />
          <XAxis dataKey="day" stroke="#a0aec0" fontSize={12} />
          <YAxis stroke="#a0aec0" fontSize={12} />
          <Tooltip contentStyle={{ background: '#1a202c', border: '1px solid #4a5568', color: '#e2e8f0' }} />
          <Line type="monotone" dataKey="ideal" stroke="#4a5568" strokeDasharray="5 5" name="Ideal" />
          <Line type="monotone" dataKey="actual" stroke="#6366f1" strokeWidth={2} name="Actual" dot={{ r: 3 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function ActivityChart({ activities }) {
  const byDay = {};
  (activities || []).forEach(a => {
    const d = new Date(a.timestamp).toLocaleDateString(undefined, { weekday: 'short' });
    byDay[d] = byDay[d] || { day: d, commits: 0, prs: 0, issues: 0 };
    if (a.type === 'commit') byDay[d].commits++;
    else if (a.type === 'pr') byDay[d].prs++;
    else byDay[d].issues++;
  });
  const chartData = Object.values(byDay);
  if (chartData.length === 0) return null;
  return (
    <div style={styles.chart}>
      <h3 style={styles.sectionTitle}>Activity Breakdown</h3>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={chartData} margin={{ top: 5, right: 20, bottom: 5, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#2d3748" />
          <XAxis dataKey="day" stroke="#a0aec0" fontSize={12} />
          <YAxis stroke="#a0aec0" fontSize={12} />
          <Tooltip contentStyle={{ background: '#1a202c', border: '1px solid #4a5568', color: '#e2e8f0' }} />
          <Bar dataKey="commits" stackId="a" fill="#6366f1" name="Commits" />
          <Bar dataKey="prs" stackId="a" fill="#10b981" name="PRs" />
          <Bar dataKey="issues" stackId="a" fill="#f59e0b" name="Issues" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function App() {
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');
  const { data: activities, loading: l1, error: e1 } = useApi('/activities');
  const { data: contributors, loading: l2, error: e2 } = useApi('/contributors');
  const { data: burndownData, loading: l3, error: e3 } = useApi('/burndown');

  const isLoading = l1 && l2 && l3;

  return (
    <div style={styles.app}>
      <header style={styles.header}>
        <h1 style={styles.logo}>⚡ DevPulse</h1>
        <span style={styles.subtitle}>Real-time Developer Activity Dashboard</span>
      </header>
      <SearchBar value={search} onChange={setSearch} typeFilter={typeFilter} onTypeChange={setTypeFilter} />
      {(e1 || e2 || e3) && (
        <div style={styles.errorBar}>⚠ API error — showing cached/mock data. Retrying...</div>
      )}
      <div style={styles.grid}>
        <div style={styles.mainCol}>
          <ActivityFeed activities={activities} search={search} typeFilter={typeFilter} />
          <ActivityChart activities={activities} />
          <BurndownChart data={burndownData} />
        </div>
        <div style={styles.sideCol}>
          <Leaderboard contributors={contributors} />
        </div>
      </div>
      <footer style={styles.footer}>DevPulse • Auto-refreshes every {POLL_INTERVAL / 1000}s</footer>
    </div>
  );
}

const styles = {
  app: { minHeight: '100vh', background: '#0f1117', color: '#e2e8f0', fontFamily: "'Inter', system-ui, sans-serif", padding: '0 24px 24px' },
  header: { display: 'flex', alignItems: 'baseline', gap: 16, padding: '20px 0 12px' },
  logo: { margin: 0, fontSize: 24, color: '#6366f1' },
  subtitle: { color: '#a0aec0', fontSize: 14 },
  searchBar: { display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 16 },
  searchInput: { flex: 1, minWidth: 200, padding: '10px 14px', borderRadius: 8, border: '1px solid #2d3748', background: '#1a202c', color: '#e2e8f0', fontSize: 14, outline: 'none' },
  filterButtons: { display: 'flex', gap: 6 },
  filterBtn: { padding: '6px 14px', borderRadius: 6, border: '1px solid #2d3748', background: '#1a202c', color: '#a0aec0', cursor: 'pointer', fontSize: 13 },
  filterBtnActive: { background: '#6366f1', color: '#fff', borderColor: '#6366f1' },
  grid: { display: 'grid', gridTemplateColumns: '1fr 320px', gap: 20 },
  mainCol: { display: 'flex', flexDirection: 'column', gap: 20 },
  sideCol: {},
  sectionTitle: { margin: '0 0 12px', fontSize: 16, color: '#cbd5e0' },
  feed: { background: '#1a202c', borderRadius: 10, padding: 16, maxHeight: 480, overflowY: 'auto' },
  empty: { color: '#718096', fontSize: 13 },
  feedItem: { padding: '10px 12px', marginBottom: 8, borderRadius: 6, background: '#0f1117', borderLeft: '3px solid #6366f1' },
  feedHeader: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, marginBottom: 4 },
  feedIcon: { fontSize: 14 },
  feedAuthor: { color: '#e2e8f0' },
  feedType: { color: '#718096', fontSize: 11 },
  feedTime: { marginLeft: 'auto', color: '#4a5568', fontSize: 11 },
  feedBody: { display: 'flex', flexDirection: 'column', gap: 2, paddingLeft: 22 },
  feedRepo: { color: '#6366f1', fontSize: 12 },
  feedMsg: { color: '#a0aec0', fontSize: 13 },
  leaderboard: { background: '#1a202c', borderRadius: 10, padding: 16 },
  leaderRow: { display: 'flex', alignItems: 'center', gap: 10, padding: '8px 0', borderBottom: '1px solid #2d3748' },
  leaderRank: { color: '#718096', fontSize: 12, width: 24 },
  avatar: { width: 28, height: 28, borderRadius: '50%' },
  leaderName: { flex: 1, fontSize: 13, color: '#e2e8f0' },
  leaderScore: { color: '#6366f1', fontWeight: 700, fontSize: 14 },
  chart: { background: '#1a202c', borderRadius: 10, padding: 16 },
  errorBar: { background: '#744210', color: '#fbd38d', padding: '8px 14px', borderRadius: 8, fontSize: 13, marginBottom: 12 },
  footer: { textAlign: 'center', color: '#4a5568', fontSize: 12, marginTop: 24, padding: 12 },
};