/**
 * ECharts for the browser, with only what the chart kinds use (WAC-10g).
 *
 * `kinds.mjs` names these series and components. Importing them one by one,
 * not the whole library, keeps the chat's bundle near a third of the full
 * build. `LiveChart` loads this module on first use, so a chat with no chart
 * never downloads it. A kind that needs a new series or component adds it
 * here, and `charts.test.ts` draws every kind through this list.
 */
import * as echarts from "echarts/core";
import {
  BarChart, BoxplotChart, CustomChart, FunnelChart, HeatmapChart, LineChart, PieChart,
  RadarChart, ScatterChart,
} from "echarts/charts";
import {
  CalendarComponent, GraphicComponent, GridComponent, LegendComponent, MarkLineComponent,
  PolarComponent, RadarComponent, TitleComponent, TooltipComponent, VisualMapComponent,
} from "echarts/components";
import { SVGRenderer } from "echarts/renderers";

echarts.use([
  BarChart, BoxplotChart, CustomChart, FunnelChart, HeatmapChart, LineChart, PieChart,
  RadarChart, ScatterChart,
  CalendarComponent, GraphicComponent, GridComponent, LegendComponent, MarkLineComponent,
  PolarComponent, RadarComponent, TitleComponent, TooltipComponent, VisualMapComponent,
  SVGRenderer,
]);

export { echarts };
