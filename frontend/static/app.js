let map, markerGroup, centerMarker, occupancyChart;

// 지도 초기화
function initMap() {
  map = L.map('map').setView([37.5665, 126.9780], 13);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors',
    maxZoom: 19,
  }).addTo(map);
  markerGroup = L.layerGroup().addTo(map);
}

// 검색 실행
async function runSearch() {
  const address = document.getElementById('addressInput').value.trim();
  if (!address) { alert('주소를 입력해주세요.'); return; }

  const radius = parseFloat(document.getElementById('radiusSelect').value);
  const platforms = [];
  if (document.getElementById('chkAirbnb').checked) platforms.push('airbnb');
  if (document.getElementById('chkBooking').checked) platforms.push('booking');
  if (document.getElementById('chkTrip').checked) platforms.push('tripdotcom');
  if (document.getElementById('chkAgoda').checked) platforms.push('agoda');
  if (platforms.length === 0) { alert('플랫폼을 하나 이상 선택해주세요.'); return; }

  setLoading(true);

  try {
    const res = await fetch('/api/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ address, radius_km: radius, platforms, fetch_occupancy: true }),
    });

    if (!res.ok) {
      const err = await res.json();
      alert(`오류: ${err.detail || '검색 실패'}`);
      return;
    }

    const data = await res.json();
    renderResults(data);
  } catch (e) {
    alert('서버 연결 오류. 백엔드가 실행 중인지 확인해주세요.');
    console.error(e);
  } finally {
    setLoading(false);
  }
}

function renderResults(data) {
  const { center, listings, stats, radius_km } = data;

  // 지도 이동
  markerGroup.clearLayers();
  map.setView([center.lat, center.lng], 14);

  // 중심 마커
  if (centerMarker) map.removeLayer(centerMarker);
  centerMarker = L.circleMarker([center.lat, center.lng], {
    radius: 10, color: '#667eea', fillColor: '#667eea', fillOpacity: 0.3, weight: 2,
  }).addTo(map);

  // 반경 원
  L.circle([center.lat, center.lng], {
    radius: radius_km * 1000, color: '#667eea', fillOpacity: 0.04, weight: 1.5, dashArray: '6',
  }).addTo(markerGroup);

  // 숙소 마커
  listings.forEach((listing, idx) => {
    if (!listing.lat || !listing.lng) return;
    const isMulti = listing.is_multi_platform;
    const pc = PLATFORM_COLORS[listing.platform] || { color: '#888', fill: '#888' };

    const marker = isMulti
      ? L.circleMarker([listing.lat, listing.lng], {
          radius: 11, color: '#fff', fillColor: '#7c3aed',
          fillOpacity: 1, weight: 3,
        })
      : L.circleMarker([listing.lat, listing.lng], {
          radius: 8, color: pc.color, fillColor: pc.fill, fillOpacity: 0.85, weight: 2,
        });

    marker.addTo(markerGroup);
    marker.bindPopup(buildPopupHtml(listing));
    marker.on('click', () => highlightListingItem(idx));
  });

  // 통계 업데이트
  renderStats(stats, listings.length);

  // 숙소 목록 렌더링
  renderListingsList(listings);

  document.getElementById('statsPanel').style.display = 'block';
}

const PLATFORM_LABELS = {
  airbnb: '에어비앤비',
  yanolja: '야놀자',
  booking: '부킹닷컴',
  tripdotcom: '트립닷컴',
  agoda: '아고다',
};

const PLATFORM_COLORS = {
  airbnb:     { color: '#e74c3c', fill: '#e74c3c' },
  yanolja:    { color: '#1a6fc4', fill: '#1a6fc4' },
  booking:    { color: '#003580', fill: '#003580' },
  tripdotcom: { color: '#00a651', fill: '#00a651' },
  agoda:      { color: '#eb5c28', fill: '#eb5c28' },
};

const PLATFORM_ICONS = {
  airbnb: '🏠', yanolja: '🏨', booking: '🏩', tripdotcom: '✈️', agoda: '🌏',
};

function buildPopupHtml(l) {
  const rate = l.occupancy_rate;
  const vacancy = rate !== null ? (100 - rate).toFixed(1) : null;

  if (l.is_multi_platform && l.cross_platforms) {
    // 멀티 플랫폼 팝업
    const platformRows = l.cross_platforms.map(cp => {
      const label = PLATFORM_LABELS[cp.platform] || cp.platform;
      const dotColor = (PLATFORM_COLORS[cp.platform] || {}).color || '#888';
      const priceStr = cp.price ? `<b>${cp.price.toLocaleString()}원</b>` : '-';
      const rateStr = cp.occupancy_rate !== null && cp.occupancy_rate !== undefined
        ? `예약 ${cp.occupancy_rate}%` : '';
      const linkStr = cp.url
        ? `<a href="${escHtml(cp.url)}" target="_blank" style="color:#7c3aed;font-size:11px">바로가기 →</a>` : '';
      return `<tr>
        <td style="padding:3px 6px 3px 0">
          <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${dotColor};margin-right:4px"></span>
          <b style="font-size:12px">${label}</b>
        </td>
        <td style="padding:3px 4px;font-size:12px">${priceStr}</td>
        <td style="padding:3px 0;font-size:11px;color:#5b21b6">${rateStr}</td>
        <td style="padding:3px 0 3px 6px">${linkStr}</td>
      </tr>`;
    }).join('');

    return `
      <div class="popup-title" style="color:#7c3aed">${escHtml(l.name || '이름 없음')}</div>
      <div style="font-size:11px;color:#7c3aed;margin-bottom:6px">📌 ${l.cross_platforms.length}개 플랫폼 등록</div>
      <table style="border-collapse:collapse;width:100%">${platformRows}</table>
    `;
  }

  const label = PLATFORM_LABELS[l.platform] || l.platform;
  return `
    <div class="popup-title">${escHtml(l.name || '이름 없음')}</div>
    <div style="font-size:12px;color:#666">${label} · ${l.room_type || ''}</div>
    <div class="popup-rates">
      ${rate !== null
        ? `<span class="rate-badge occupancy">예약율 ${rate}%</span><span class="rate-badge vacancy">공실율 ${vacancy}%</span>`
        : '<span class="rate-badge no-data">예약율 미집계</span>'}
      ${l.price ? `<span class="rate-badge platform">${l.price.toLocaleString()}원</span>` : ''}
    </div>
    ${l.url ? `<a class="popup-link" href="${escHtml(l.url)}" target="_blank">숙소 바로가기 →</a>` : ''}
  `;
}

function renderStats(stats, total) {
  document.getElementById('avgOccupancy').textContent =
    stats.avg_occupancy_rate !== null ? `${stats.avg_occupancy_rate}%` : '-';
  document.getElementById('avgVacancy').textContent =
    stats.avg_vacancy_rate !== null ? `${stats.avg_vacancy_rate}%` : '-';
  document.getElementById('totalCount').textContent = total;
  document.getElementById('avgPrice').textContent =
    stats.avg_price_krw ? `${stats.avg_price_krw.toLocaleString()}원` : '-';

  // 플랫폼별 배지
  const breakdown = document.getElementById('platformBreakdown');
  breakdown.innerHTML = Object.entries(stats.by_platform || {})
    .map(([p, c]) => `<span class="platform-badge ${p}">${PLATFORM_LABELS[p] || p} ${c}개</span>`)
    .join('');
}

function renderListingsList(listings) {
  const el = document.getElementById('listingsList');
  if (listings.length === 0) {
    el.innerHTML = '<p style="padding:16px;color:#999;font-size:14px">검색 결과가 없습니다.</p>';
    return;
  }

  el.innerHTML = listings.map((l, idx) => {
    const rate = l.occupancy_rate;
    const vacancy = rate !== null ? (100 - rate).toFixed(1) : null;
    const isMulti = l.is_multi_platform && l.cross_platforms;
    const icon = isMulti ? '🏨' : (PLATFORM_ICONS[l.platform] || '🏨');
    const thumb = l.image
      ? `<img class="listing-thumb" src="${escHtml(l.image)}" alt="" loading="lazy" onerror="this.style.display='none'">`
      : `<div class="listing-thumb-placeholder${isMulti ? ' multi' : ''}">${icon}</div>`;

    // 플랫폼 표시 영역
    const platformMeta = isMulti
      ? l.cross_platforms.map(cp => {
          const dotColor = (PLATFORM_COLORS[cp.platform] || {}).color || '#888';
          return `<span style="display:inline-flex;align-items:center;gap:3px;margin-right:6px">
            <span style="width:7px;height:7px;border-radius:50%;background:${dotColor};display:inline-block"></span>
            <span style="font-size:11px">${PLATFORM_LABELS[cp.platform] || cp.platform}</span>
          </span>`;
        }).join('')
      : `<span class="platform-dot platform-dot--${l.platform}"></span>${PLATFORM_LABELS[l.platform] || l.platform}
         ${l.rating ? ` · ⭐ ${l.rating}` : ''}
         ${l.review_count ? ` (${l.review_count}개)` : ''}`;

    // 가격 표시: 멀티면 플랫폼별 가격 비교
    const priceHtml = isMulti
      ? l.cross_platforms.map(cp => cp.price
          ? `<span class="rate-badge platform" style="font-size:10px">${PLATFORM_LABELS[cp.platform]?.slice(0,3)} ₩${cp.price.toLocaleString()}</span>`
          : '').join('')
      : (l.price ? `<span class="rate-badge platform">${l.price.toLocaleString()}원</span>` : '');

    const rateHtml = rate !== null
      ? `<span class="rate-badge occupancy">예약 ${rate}%</span><span class="rate-badge vacancy">공실 ${vacancy}%</span>`
      : '<span class="rate-badge no-data">예약율 미집계</span>';

    const itemClass = `listing-item${isMulti ? ' multi-platform' : ''}`;

    return `
      <div class="${itemClass}" id="item-${idx}" onclick="focusListing(${idx}, ${l.lat}, ${l.lng})">
        ${thumb}
        <div class="listing-info">
          <div class="listing-name">${isMulti ? '📌 ' : ''}${escHtml(l.name || '이름 없음')}</div>
          <div class="listing-meta">${platformMeta}</div>
          <div class="listing-rates">
            ${rateHtml}
            ${priceHtml}
          </div>
        </div>
      </div>
    `;
  }).join('');

  // 예약율 분포 차트 업데이트
  updateOccupancyChart(listings);
}

function updateOccupancyChart(listings) {
  const rates = listings
    .map(l => l.occupancy_rate)
    .filter(r => r !== null);

  const buckets = [0, 0, 0, 0, 0]; // 0-20, 20-40, 40-60, 60-80, 80-100
  rates.forEach(r => {
    const idx = Math.min(Math.floor(r / 20), 4);
    buckets[idx]++;
  });

  const labels = ['0-20%', '20-40%', '40-60%', '60-80%', '80-100%'];
  const ctx = document.getElementById('occupancyChart').getContext('2d');

  if (occupancyChart) occupancyChart.destroy();
  occupancyChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: '숙소 수',
        data: buckets,
        backgroundColor: [
          '#a8d8ea', '#7ec8e3', '#5b9bd5', '#3a7bd5', '#2c5282',
        ],
        borderRadius: 4,
      }],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, ticks: { stepSize: 1 } },
        x: { grid: { display: false } },
      },
    },
  });
}

function focusListing(idx, lat, lng) {
  document.querySelectorAll('.listing-item').forEach(el => el.classList.remove('active'));
  const el = document.getElementById(`item-${idx}`);
  if (el) { el.classList.add('active'); el.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
  if (lat && lng) map.setView([lat, lng], 16);
}

function highlightListingItem(idx) {
  document.querySelectorAll('.listing-item').forEach(el => el.classList.remove('active'));
  const el = document.getElementById(`item-${idx}`);
  if (el) { el.classList.add('active'); el.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
}

function setLoading(on) {
  document.getElementById('loadingOverlay').style.display = on ? 'flex' : 'none';
  document.getElementById('searchBtn').disabled = on;
}

function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// 엔터 키 검색
document.addEventListener('DOMContentLoaded', () => {
  initMap();
  document.getElementById('addressInput').addEventListener('keydown', e => {
    if (e.key === 'Enter') runSearch();
  });
});
