import { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, ScatterChart, type BarSeriesOption, type ScatterSeriesOption } from 'echarts/charts'
import {
  DataZoomComponent, GridComponent, TooltipComponent,
  type DataZoomComponentOption, type GridComponentOption, type TooltipComponentOption,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

// Register only what the app draws, so the bundle carries only these charts and components.
echarts.use([BarChart, ScatterChart, GridComponent, TooltipComponent, DataZoomComponent, CanvasRenderer])

/** Option type limited to the registered charts and components. */
export type ChartOption = echarts.ComposeOption<
  BarSeriesOption | ScatterSeriesOption | GridComponentOption | TooltipComponentOption | DataZoomComponentOption
>

/**
 * An ECharts canvas that fills its parent (give the parent a fixed height — layout rule 1).
 *
 * Initialized once; later `option` changes go through setOption, which merges and animates the
 * transition. Charts with the same `group` are connected: zooming or hovering one moves the others,
 * which keeps side-by-side timelines aligned (connect only flags the group name; disposed charts
 * drop out on their own). Memoize `option`, or every render re-applies it.
 */
export function EChart({ option, group }: { option: ChartOption; group?: string }) {
  const el = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)

  useEffect(() => {
    const node = el.current
    if (!node) return undefined
    const instance = echarts.init(node)
    chart.current = instance
    if (group) {
      instance.group = group
      echarts.connect(group)
    }
    const observer = new ResizeObserver(() => instance.resize())
    observer.observe(node)
    return () => {
      observer.disconnect()
      instance.dispose()
      chart.current = null
    }
  }, [group])

  useEffect(() => {
    // A normal merge never removes components, so a zoom slider from a longer series would
    // survive a switch to a short one; replace dataZoom (and series) wholesale instead. Unlike
    // notMerge this keeps the transition animation and doesn't reset everything on a theme flip.
    chart.current?.setOption(option, { replaceMerge: ['dataZoom', 'series'] })
  }, [option, group])

  return <div ref={el} className="echart" />
}

export type ChartTheme = {
  text: string
  muted: string
  border: string
  panel: string
  neg: string
  fontFamily: string
}

const readTheme = (): ChartTheme => {
  const root = getComputedStyle(document.documentElement)
  const token = (name: string) => root.getPropertyValue(name).trim()
  return {
    text: token('--text'),
    muted: token('--muted'),
    border: token('--border'),
    panel: token('--panel'),
    neg: token('--neg'),
    fontFamily: getComputedStyle(document.body).fontFamily,
  }
}

/** Theme colors resolved from the CSS variables (a canvas can't read `var()`), refreshed when the
 *  system switches between light and dark. Pass the result into the option builders. */
export function useChartTheme(): ChartTheme {
  const [theme, setTheme] = useState(readTheme)
  useEffect(() => {
    const scheme = matchMedia('(prefers-color-scheme: dark)')
    const refresh = () => setTheme(readTheme())
    scheme.addEventListener('change', refresh)
    return () => scheme.removeEventListener('change', refresh)
  }, [])
  return theme
}
