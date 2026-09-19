import type { ChartOption, ChartTheme } from './EChart.tsx'
import { dayLabel, monthLabel, num } from '../format.ts'

/*
 * Option builders for the app's charts. Series keep ECharts' stock v6 palette; only chrome
 * (text, grid lines, tooltip, zoom slider) follows our light/dark tokens, plus negatives in --neg.
 */

/** A month series is only worth a zoom slider past this many bars. */
const ZOOM_AFTER_MONTHS = 36

const chrome = (t: ChartTheme) => ({
  backgroundColor: 'transparent',
  textStyle: { color: t.muted, fontFamily: t.fontFamily },
})

const tooltip = (t: ChartTheme) => ({
  backgroundColor: t.panel,
  borderColor: t.border,
  textStyle: { color: t.text, fontSize: 12 },
})

const axisLine = (t: ChartTheme) => ({ lineStyle: { color: t.border } })
const splitLine = (t: ChartTheme) => ({ lineStyle: { color: t.border, type: 'dashed' as const } })
const axisLabel = (t: ChartTheme) => ({ color: t.muted, fontSize: 11 })

/** Compact value-axis labels: 150000 -> 150k, 1500 -> 1.5k. */
const compact = (v: number) => (Math.abs(v) >= 1000 ? `${num(v / 1000, v % 1000 ? 1 : 0)}k` : num(v))

export type MonthPoint = { month_ts: string; value: number }

/**
 * Bars over a continuous month timeline (the API already fills empty months). Long timelines get
 * a range slider plus drag-to-pan and Ctrl+wheel zoom; negative months are drawn in --neg.
 */
export function monthlyBars(
  rows: readonly MonthPoint[],
  { name, color }: { name: string; color?: string },
  t: ChartTheme,
): ChartOption {
  const zoom = rows.length > ZOOM_AFTER_MONTHS
  // Left alone, one slightly negative (refund-heavy) month drags the axis floor down a whole tick
  // step and wastes a slice of the plot. Floor just below the lowest value instead, and hide that
  // floor's (non-round) label; the other ticks stay round.
  const lowest = Math.min(0, ...rows.map((r) => r.value))
  const floor = lowest < 0 ? Math.floor(lowest * 1.15) : 0
  return {
    ...chrome(t),
    grid: { top: 12, right: 12, bottom: zoom ? 56 : 8, left: 8 },
    tooltip: {
      ...tooltip(t),
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      valueFormatter: (v) => num(Number(v)),
    },
    xAxis: {
      type: 'category',
      data: rows.map((r) => monthLabel(r.month_ts)),
      axisLine: axisLine(t),
      axisTick: { show: false },
      axisLabel: { ...axisLabel(t), hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      min: floor,
      axisLabel: { ...axisLabel(t), formatter: compact, showMinLabel: floor === 0 },
      splitLine: splitLine(t),
    },
    dataZoom: zoom
      ? [
          // drag to pan; zooming needs Ctrl+wheel, so a plain wheel still scrolls the page
          { type: 'inside', zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: false },
          {
            type: 'slider',
            height: 18,
            bottom: 8,
            borderColor: t.border,
            textStyle: { color: t.muted, fontSize: 11 },
          },
        ]
      : [],
    series: [{
      type: 'bar',
      name,
      barMaxWidth: 28,
      itemStyle: { borderRadius: [3, 3, 0, 0], ...(color ? { color } : {}) },
      data: rows.map((r) => (r.value < 0 ? { value: r.value, itemStyle: { color: t.neg } } : r.value)),
    }],
  }
}

export type RankedPoint = { label: string; value: number }

/** Horizontal bars, largest first, one readable label per bar and the value at the bar's end. */
export function rankedBars(rows: readonly RankedPoint[], { name }: { name: string }, t: ChartTheme): ChartOption {
  return {
    ...chrome(t),
    grid: { top: 4, right: 72, bottom: 4, left: 8 },
    tooltip: {
      ...tooltip(t),
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      valueFormatter: (v) => num(Number(v)),
    },
    xAxis: { type: 'value', axisLabel: { show: false }, splitLine: { show: false } },
    yAxis: {
      type: 'category',
      inverse: true,
      data: rows.map((r) => r.label),
      axisLine: { show: false },
      axisTick: { show: false },
      // interval 0: a label on EVERY bar; long CJK names are cut with an ellipsis (full name in the tooltip)
      axisLabel: { color: t.text, fontSize: 12, interval: 0, width: 140, overflow: 'truncate' },
    },
    series: [{
      type: 'bar',
      name,
      barMaxWidth: 18,
      itemStyle: { borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: 'right', color: t.muted, fontSize: 11, formatter: (p) => num(Number(p.value)) },
      // a net-negative total (refunds outweighing purchases) gets --neg and its label on the left
      data: rows.map((r) => (r.value < 0
        ? { value: r.value, itemStyle: { color: t.neg, borderRadius: [4, 0, 0, 4] }, label: { position: 'left' as const } }
        : r.value)),
    }],
  }
}

export type CadencePoint = { visit_date: string; days_since_last: number }

/** Days since the previous visit, over time. */
export function cadenceScatter(rows: readonly CadencePoint[], t: ChartTheme): ChartOption {
  return {
    ...chrome(t),
    grid: { top: 24, right: 16, bottom: 8, left: 8 },
    tooltip: {
      ...tooltip(t),
      trigger: 'item',
      formatter: (params) => {
        const row = Array.isArray(params) ? undefined : rows[params.dataIndex]
        return row ? `${dayLabel(row.visit_date)}<br/>${num(row.days_since_last)} days since last visit` : ''
      },
    },
    xAxis: { type: 'time', axisLine: axisLine(t), axisLabel: { ...axisLabel(t), hideOverlap: true } },
    yAxis: {
      type: 'value',
      name: 'days',
      nameTextStyle: { color: t.muted, fontSize: 11 },
      axisLabel: axisLabel(t),
      splitLine: splitLine(t),
    },
    series: [{
      type: 'scatter',
      symbolSize: 7,
      itemStyle: { opacity: 0.8 },
      data: rows.map((r) => [r.visit_date, r.days_since_last]),
    }],
  }
}
