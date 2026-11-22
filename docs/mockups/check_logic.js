// Runs an artboard's logic outside the browser: node check_logic.js project/Main.dc.html
// It executes renderVals() with a stub of the runtime and lists template values that the markup
// uses but the logic does not return (and the reverse). It does not render anything, so it
// cannot see layout faults.
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const js = src.split(/<script type="text\/x-dc"[^>]*>/)[1].split('</script>')[0];
const html = src.split('<x-dc>')[1].split('</x-dc>')[0];
class DCLogic { setState(s) { this.state = Object.assign({}, this.state || {}, s); } }
const Component = new Function('DCLogic', js + '; return Component;')(DCLogic);
const c = new Component();
const t0 = Date.now();
const out = c.renderVals();
const holes = [...new Set([...html.matchAll(/\{\{\s*([A-Za-z0-9_$.]+)\s*\}\}/g)].map((m) => m[1]))];
const loopVars = new Set([...html.matchAll(/<sc-for[^>]*\bas="([^"]+)"/g)].map((m) => m[1]));
const top = holes.filter((h) => !loopVars.has(h.split('.')[0]) && h !== 'true' && h !== 'false');
console.log('first render', Date.now() - t0, 'ms');
console.log('used but not returned:', top.filter((h) => !(h.split('.')[0] in out)));
console.log('returned but not used:', Object.keys(out).filter((k) => !holes.some((h) => h.split('.')[0] === k)));
