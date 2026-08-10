import { ref } from 'vue'

// 温度单位切换（摄氏/华氏，localStorage 持久化）。结果区天气块与首页常驻
// 天气卡片共用同一单位开关，避免两处各自维护状态。
const TEMP_UNIT_KEY = 'sf_temp_unit'

export function useTempUnit() {
  const tempUnit = ref(localStorage.getItem(TEMP_UNIT_KEY) || 'celsius')
  function setTempUnit(unit) {
    tempUnit.value = unit
    localStorage.setItem(TEMP_UNIT_KEY, unit)
  }
  const formatTemp = (celsius, unit = tempUnit.value) => {
    if (celsius == null || Number.isNaN(celsius)) return '—'
    const value = unit === 'fahrenheit' ? Math.round(toFahrenheit(celsius)) : Math.round(celsius)
    return `${value}°${unit === 'fahrenheit' ? 'F' : 'C'}`
  }
  return { tempUnit, setTempUnit, formatTemp }
}

const toFahrenheit = (celsius) => celsius * 9 / 5 + 32

// 天气卡图标映射（按 WMO weather_code 分组）
const WEATHER_ICON_CODES = [
  { match: (code) => code <= 1, icon: '☀️' },
  { match: (code) => code === 2, icon: '⛅' },
  { match: (code) => code === 3, icon: '☁️' },
  { match: (code) => code >= 45 && code <= 48, icon: '🌫️' },
  { match: (code) => code >= 51 && code <= 57, icon: '🌦️' },
  { match: (code) => code >= 61 && code <= 67, icon: '🌧️' },
  { match: (code) => code >= 71 && code <= 77, icon: '🌨️' },
  { match: (code) => code >= 80 && code <= 86, icon: '🌧️' },
  { match: (code) => code >= 95, icon: '⛈️' },
]
export function weatherIcon(code) {
  if (code == null) return '🌡️'
  const entry = WEATHER_ICON_CODES.find((item) => item.match(code))
  return entry ? entry.icon : '🌡️'
}

// 穿搭提示行（参考 wardrobe 参考实现的 weatherHints 规则）
const WEATHER_HINTS = {
  rainy: '降雨概率高，带伞并选防水外层与防滑鞋',
  cold: '体感偏冷，注意保暖叠穿',
  mild: '体感偏凉，适合薄外套或长袖叠穿',
  hot: '天气炎热，优先透气轻薄面料并注意防晒',
  windy: '风力较大，避免宽松裙摆与易飘单品',
  nice: '天气宜人，常规穿搭即可',
}
export function weatherHint(day) {
  const temp = day.feels_like_c ?? day.temperature_max_c
  const precip = day.precipitation_probability_percent ?? 0
  const wind = day.wind_speed_kmh ?? 0
  if (precip > 50) return WEATHER_HINTS.rainy
  if (temp != null && temp < 10) return WEATHER_HINTS.cold
  if (temp != null && temp < 18) return WEATHER_HINTS.mild
  if (temp != null && temp > 28) return WEATHER_HINTS.hot
  if (wind > 30) return WEATHER_HINTS.windy
  return WEATHER_HINTS.nice
}
