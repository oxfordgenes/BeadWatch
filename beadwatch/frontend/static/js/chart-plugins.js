// BeadWatch — Shared Chart.js plugins.
//
// dimHiddenLegend: renders hidden legend items with dim/grey styling
// instead of a strikethrough line. Two stages keep Chart.js's public
// LegendItem.hidden contract intact:
//   1. beforeInit  → cosmetic styling on hidden items; never touches item.hidden.
//   2. afterUpdate → wrap chart.legend.draw to mask item.hidden = false only
//                    across the synchronous draw call, restoring in finally.

(function () {
    'use strict';

    Chart.register({
        id: 'dimHiddenLegend',

        beforeInit: function (chart) {
            // Use the raw user config, not chart.options. Chart.js's options
            // proxy treats some leaf properties as scriptable and invokes them
            // on read — reading generateLabels through the proxy would call it
            // with no chart, blowing up inside the default implementation.
            var opts = chart.config && chart.config.options;
            var legend = opts && opts.plugins && opts.plugins.legend;
            if (!legend) return;
            legend.labels = legend.labels || {};

            var originalGenerate = legend.labels.generateLabels;
            legend.labels.generateLabels = function (c) {
                // Chart.js calls generateLabels with `this` bound to the
                // legend instance. Preserve that for any custom generator.
                var base = (typeof originalGenerate === 'function'
                    ? originalGenerate.call(this, c)
                    : Chart.defaults.plugins.legend.labels.generateLabels.call(this, c));

                base.forEach(function (item) {
                    var realHidden;
                    if (typeof item.hidden === 'boolean') {
                        realHidden = item.hidden;
                    } else if (typeof item.datasetIndex === 'number') {
                        var meta = c.getDatasetMeta(item.datasetIndex);
                        realHidden = meta && meta.hidden === true;
                    } else {
                        realHidden = false;
                    }
                    item._bwHidden = realHidden;

                    if (realHidden) {
                        item.fontColor = '#475569';
                        item.fillStyle = withAlpha(item.fillStyle, 0.35);
                        if (item.strokeStyle) {
                            item.strokeStyle = withAlpha(item.strokeStyle, 0.35);
                        }
                    }
                });
                return base;
            };
        },

        afterUpdate: function (chart) {
            var legend = chart.legend;
            if (!legend || legend._bwDrawWrapped) return;
            legend._bwDrawWrapped = true;

            var originalDraw = legend.draw.bind(legend);
            legend.draw = function () {
                var items = legend.legendItems || [];
                var snapshot = items.map(function (it) { return it.hidden; });
                try {
                    for (var i = 0; i < items.length; i++) items[i].hidden = false;
                    return originalDraw();
                } finally {
                    for (var j = 0; j < items.length; j++) items[j].hidden = snapshot[j];
                }
            };
        }
    });

    // Adjust the alpha of a CSS colour. Handles every format the repo emits:
    // hex on the dashboard, hsl(...) on instruments/operators. Also handles
    // rgb/rgba/hsla. Unknown formats pass through unchanged.
    function withAlpha(color, alpha) {
        if (typeof color !== 'string') return color;
        var m;

        m = color.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
        if (m) {
            var hex = m[1];
            if (hex.length === 3) hex = hex[0]+hex[0]+hex[1]+hex[1]+hex[2]+hex[2];
            var r = parseInt(hex.slice(0,2), 16);
            var g = parseInt(hex.slice(2,4), 16);
            var b = parseInt(hex.slice(4,6), 16);
            return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
        }

        m = color.match(/^rgba?\(([^)]+)\)$/i);
        if (m) {
            var p = m[1].split(',').map(function (s) { return s.trim(); });
            if (p.length >= 3) return 'rgba(' + p[0] + ',' + p[1] + ',' + p[2] + ',' + alpha + ')';
        }

        m = color.match(/^hsla?\(([^)]+)\)$/i);
        if (m) {
            var inner = m[1].trim().split('/')[0].trim();
            var hp = inner.indexOf(',') >= 0
                ? inner.split(',').map(function (s) { return s.trim(); })
                : inner.split(/\s+/);
            if (hp.length >= 3) return 'hsla(' + hp[0] + ',' + hp[1] + ',' + hp[2] + ',' + alpha + ')';
        }

        return color;
    }
})();
