// Run from repository root: node tests/test_kitchen_callout.cjs
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('static/js/kitchen-display.js','utf8');
const start=source.indexOf('  function customerCallout(ticket){');
const end=source.indexOf('  function editNote(ticket)',start);
const context={};vm.createContext(context);vm.runInContext(source.slice(start,end),context);
const call=t=>JSON.parse(JSON.stringify(context.customerCallout(t)));
assert.deepEqual(call({notes:'Sarah'}),{name:'',notes:'Sarah'});
assert.deepEqual(call({notes:'Allergy peanuts'}),{name:'',notes:'Allergy peanuts'});
assert.deepEqual(call({notes:'Name: Captain Joe | Extra napkins'}),{name:'',notes:'Name: Captain Joe | Extra napkins'});
assert.deepEqual(call({customer_name:'Kyle DuggerTest',notes:'Extra napkins'}),{name:'Kyle DuggerTest',notes:'Extra napkins'});
assert.deepEqual(call({customer_name:'Kyle',kitchen_customer_name:'Captain Joe',notes:'Original',kitchen_notes:'Updated'}),{name:'Captain Joe',notes:'Updated'});
assert.deepEqual(call({customer_name:'Kyle',kitchen_customer_name:'',kitchen_notes:''}),{name:'',notes:''});
assert.deepEqual(call({transactions:[{customerUserId:123,customer:{firstName:'VISA'}}]}),{name:'',notes:''});
assert(source.includes("(callout.name.trim()||'#'+ticket.number)"));
console.log('PASS: notes never become names; POS name and explicit FTW overrides preserved; order-number fallback present.');
