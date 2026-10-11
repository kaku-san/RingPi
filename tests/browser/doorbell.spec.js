const { test, expect } = require('@playwright/test');

async function openDetails(page, id) {
  const details = page.locator(id);
  if ((await details.getAttribute('open')) === null) await page.locator(`${id} > summary`).click();
}

test('ring reaches the panel, captures persist, and corrections stay local', async ({ page, request }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    // A ring must not wait for a browser's potentially slow audio permission/startup.
    window.audioStarts = 0;
    window.AudioContext = class {
      constructor() { window.audioStarts += 1; }
      resume() { return new Promise(() => {}); }
    };
  });
  await page.goto('/');
  await expect(page.locator('#connection')).toContainText('Live updates connected');
  await expect(page.locator('#simulated-banner')).toBeVisible();
  await expect(page.getByRole('heading', { name: /Your doorstep/ })).toBeVisible();
  const started = Date.now();
  await page.locator('#test-ring').click();
  await expect(page.locator('#toast')).toContainText('Your test ring arrived');
  const alertMs = Date.now() - started;
  console.log(`Ring click-to-panel: ${alertMs} ms (synthetic camera, Mac localhost)`);
  expect(alertMs).toBeLessThan(1500);
  expect(await page.evaluate(() => window.audioStarts)).toBe(0);
  await expect(page.locator('#recent-events .event-row')).toHaveCount(1);
  await expect(page.locator('#recent-events .event-copy p')).toContainText('3 snapshots');
  await page.reload();
  await expect(page.locator('#recent-events .event-row')).toHaveCount(1);
  await page.getByRole('button', { name: /Familiar people/ }).click();
  await page.getByRole('button', { name: /Add a person/ }).click();
  await page.getByLabel('Name', { exact: true }).fill('Avery');
  await page.locator('#person-note').fill('Neighbour');
  await page.locator('#person-next').click();
  await expect(page.locator('#people-list')).toContainText('Avery');
  await page.locator('#enroll-dialog').getByRole('button',{name:'Done',exact:true}).click();
  await page.getByRole('button', { name: /^Activity/ }).click();
  await page.locator('#all-events .event-row').first().click();
  await page.getByLabel('Who was here?').selectOption({ label: 'Avery · manual label' });
  await page.getByLabel('A note for later').fill('Left a parcel.');
  await page.getByRole('button', { name: 'Save & mark reviewed' }).click();
  await expect(page.locator('#all-events')).toContainText('Avery');
  const body = await (await request.get('/api/events')).json();
  expect(body.events[0].person_status).toBe('manual');
  expect(body.events[0].note).toBe('Left a parcel.');
  expect(errors).toEqual([]);
});

test('snapshot preview, motion-zone settings and approval flow work', async ({ page, request }) => {
  await page.goto('/');
  await expect(page.locator('#connection')).toContainText('Live updates connected');
  await page.locator('#snapshot').click();
  await expect(page.locator('#camera-image')).toBeVisible();
  await expect(page.locator('#camera-state')).toHaveText('Camera ready');
  await page.getByRole('button', { name: 'Open device settings' }).click();
  await page.locator('#zone-x').fill('20');
  await page.locator('#zone-y').fill('15');
  await page.locator('#zone-width').fill('60');
  await page.locator('#zone-height').fill('70');
  await page.getByLabel('Keep local history for').selectOption('14');
  await page.locator('#person-alerts-enabled').check();
  await page.locator('#person-alert-seconds').fill('10');
  await page.locator('#quiet-hours-enabled').check();
  await page.locator('#quiet-start').fill('22:00');
  await page.locator('#quiet-end').fill('07:00');
  await page.locator('#summary-enabled').check();
  await page.locator('#summary-time').fill('21:00');
  await page.locator('#timezone').fill('Asia/Kolkata');
  await page.getByRole('button', { name: 'Save settings', exact: true }).click();
  await expect(page.locator('#settings-dialog')).not.toBeVisible();
  const settings = (await (await request.get('/api/status')).json()).settings;
  expect(settings.zone).toEqual({ x: 0.2, y: 0.15, width: 0.6, height: 0.7 });
  expect(settings.retention_days).toBe(14);
  expect(settings.person_alerts_enabled).toBe(true);
  expect(settings.person_alert_seconds).toBe(10);
  expect(settings.person_alert_cooldown_seconds).toBe(300);
  expect(settings.quiet_hours_enabled).toBe(true);
  expect(settings.quiet_hours_start).toBe('22:00');
  expect(settings.quiet_hours_end).toBe('07:00');
  expect(settings.summary_enabled).toBe(true);
  expect(settings.summary_time).toBe('21:00');
  expect(settings.timezone).toBe('Asia/Kolkata');
  await page.getByRole('button', { name: /Guest approval/ }).click();
  await openDetails(page, '#local-replies');
  await page.getByLabel('Local reply draft').fill('Please leave the parcel by the door.');
  await page.getByRole('button', { name: /Create reply draft/ }).click();
  await expect(page.locator('#proposal-list .status-badge').first()).toHaveText('pending');
  // Dismissal must not play anything. Approval is covered by the backend suite.
  await page.getByRole('button', { name: 'Dismiss', exact: true }).first().click();
  await expect(page.locator('#proposal-list .status-badge').first()).toHaveText('dismissed');
  await page.getByRole('button', { name: /Preview local context/ }).click();
  await expect(page.locator('#context-output')).toContainText('no external agent connected');
});

test('mobile layout stays within the viewport and has usable navigation', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.locator('#connection')).toContainText('Live updates connected');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await expect(page.locator('#test-ring')).toBeVisible();
  await page.getByRole('button', { name: /Manage your device/ }).click();
  await expect(page.locator('#quiet-hours-enabled')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('button', { name: /Guest approval/ }).click();
  await expect(page.getByRole('heading', { name: 'Guest approval.' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: 'artifacts/mobile-agent.png', fullPage: true });
});

test('profiles render hostile strings as text, never executable markup', async ({ page, request }) => {
  const created = await request.post('/api/profiles', { data: { name: '<img src=x onerror=alert(1)>', note: '<script>alert(1)</script>' } });
  const profile = await created.json();
  let dialog = false;
  page.on('dialog', async item => { dialog = true; await item.dismiss(); });
  await page.goto('/#people');
  await expect(page.locator('#people-list')).toContainText('<img src=x onerror=alert(1)>');
  expect(await page.locator('#people-list img').count()).toBe(0);
  expect(dialog).toBeFalsy();
  await request.delete(`/api/profiles/${profile.id}`);
});

test('bot setup saves locally without keys or probes and pending providers stay honest', async ({page,request}) => {
  await page.goto('/#agent');
  await openDetails(page, '#bot-tools');
  await expect(page.locator('#bot-state')).toHaveText('Not connected');
  await page.locator('#bot-endpoint').fill('https://bot.example/my-routine');
  await page.locator('#bot-destination').fill('My Main Bot');
  await page.getByRole('button',{name:'Save bot setup',exact:true}).click();
  await expect(page.locator('#toast')).toContainText('Saving never probes');
  await expect(page.locator('#bot-test')).toBeDisabled();
  await expect(page.locator('#bot-ring-test')).toBeDisabled();
  await expect(page.locator('#bot-key-state')).toContainText('Bearer key missing');
  expect((await (await request.get('/api/bots')).json()).bots[0].config.enabled).toBe(false);
  await page.locator('#bot-provider').selectOption('dot');
  await expect(page.locator('#bot-fields')).not.toBeVisible();
  await expect(page.locator('#bot-detail')).toContainText('wake trigger is not verified');
  await page.locator('#bot-provider').selectOption('muse');
  await expect(page.locator('#bot-detail')).toContainText('Exact product');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});

test('Grok accepted delivery needs destination confirmation before enabling sharing', async ({page,request}) => {
  const status=await (await request.get('/api/bots')).json();
  const grok=status.bots[0];grok.credential_present=true;grok.state='configured';
  grok.config={provider:'grok',endpoint:'https://bot.example/my-routine',session_id:'',session_key:'',destination:'My Main Bot',delivery:'owning_bot_chat',share_names:false,enabled:false};
  let deliveries=[],tests=0;
  await page.route('**/api/bots**',async route=>{
    const req=route.request(),path=new URL(req.url()).pathname;
    if(path==='/api/bots/grok/test') { tests++;grok.verified=true;grok.state='test_accepted';deliveries=[{id:'fixture',provider:'grok',event_id:'synthetic-event',synthetic:true,state:'accepted',attempts:1,detail:'Grok accepted and started the routine. Check the owning Bot chat for completion.',created_at:new Date().toISOString(),output:'',approval:null}];return route.fulfill({status:202,json:{synthetic:true,images:false}}); }
    if(path==='/api/bots/grok/confirm') {grok.confirmed=true;grok.state='test_verified';return route.fulfill({json:status});}
    if(path==='/api/bots/deliveries') return route.fulfill({json:{deliveries}});
    if(path==='/api/bots') {if(req.method()==='PUT'){const body=req.postDataJSON();grok.config=body;grok.enabled=body.enabled;grok.state=body.enabled?'enabled':'test_verified';}return route.fulfill({json:status});}
    return route.fallback();
  });
  page.on('dialog',d=>d.accept());
  await page.goto('/#agent');
  await openDetails(page, '#bot-tools');
  await expect(page.locator('#bot-enabled')).toBeDisabled();
  await page.locator('#bot-test').click();
  await expect(page.locator('#bot-deliveries')).toContainText('SYNTHETIC · accepted');
  await expect(page.locator('#bot-deliveries')).toContainText('Check the owning Bot chat');
  await expect(page.locator('#bot-enabled')).toBeDisabled();
  await page.locator('#bot-confirm').click();
  await expect(page.locator('#bot-enabled')).toBeEnabled();
  await page.locator('#bot-enabled').check();
  await page.getByRole('button',{name:'Save bot setup',exact:true}).click();
  await expect(page.locator('#bot-state')).toHaveText('Ring sharing enabled');
  expect(tests).toBe(1);expect(grok.config.share_names).toBeUndefined();expect(grok.config.enabled).toBe(true);
});

test('paired ring confirms destination and data before sending, and cancels cleanly', async ({page,request}) => {
  const status=await (await request.get('/api/bots')).json();
  const grok=status.bots[0];
  Object.assign(grok,{credential_present:true,state:'enabled',enabled:true,verified:true,confirmed:true,revision:'a'.repeat(32),
    config:{provider:'grok',endpoint:'https://bot.example/doorbell',session_id:'',session_key:'',destination:'Fixture Doorbell Bot',delivery:'owning_bot_chat',share_names:false,enabled:true}});
  await page.route('**/api/bots',route=>route.fulfill({json:status}));
  await page.route('**/api/bots/deliveries',route=>route.fulfill({json:{deliveries:[]}}));
  const fixture=(await (await request.post('/api/ring',{data:{source:'browser'}})).json()).event;
  let sent=[],accept=false,dialogs=[];
  await page.route('**/api/ring/test-with-bot',route=>{
    sent.push(route.request().postDataJSON());
    return route.fulfill({status:201,json:{event:{...fixture,kind:'test',source:'browser_bot'},duplicate:false}});
  });
  page.on('dialog',async d=>{dialogs.push(d.message());await (accept?d.accept():d.dismiss());});
  await page.goto('/#agent');
  await openDetails(page, '#bot-tools');
  await expect(page.locator('#bot-ring-test')).toBeEnabled();
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.screenshot({path:'artifacts/paired-bot-ring.png',fullPage:true});
  await page.locator('#bot-ring-test').click();
  await expect(page.locator('#bot-ring-test')).toBeEnabled();
  expect(sent).toHaveLength(0);
  expect(dialogs[0]).toContain('Fixture Doorbell Bot');
  expect(dialogs[0]).toContain('TEST ring');
  expect(dialogs[0]).toContain('name line or unknown');
  expect(dialogs[0]).toContain('short caption');
  expect(dialogs[0]).not.toContain('person/dog');
  expect(dialogs[0]).toContain('Photos and face embeddings stay on the Pi');
  accept=true;
  await page.locator('#bot-ring-test').click();
  await expect(page.locator('#toast')).toContainText('One message is queued');
  expect(sent).toEqual([{provider:'grok',revision:'a'.repeat(32),confirmed:true}]);
  await expect(page.locator('#bot-ring-test')).toBeEnabled();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
  await page.locator('#bot-destination').fill('Unsaved different destination');
  await expect(page.locator('#bot-ring-test')).toBeDisabled();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy();
});

test('image scope defaults off, needs owner permission and shows cloud disclosure', async ({page,request}) => {
  const status=await (await request.get('/api/bots')).json();
  const grok=status.bots[0];
  Object.assign(grok,{credential_present:true,state:'enabled',enabled:true,verified:true,confirmed:true,
    image_owner_allowed:false,image_transport_ready:false,images:false,revision:'b'.repeat(32),
    config:{provider:'grok',endpoint:'https://bot.example/doorbell',session_id:'',session_key:'',destination:'Image Test Bot',delivery:'owning_bot_chat',share_names:false,share_images:false,enabled:true}});
  let writes=[];
  await page.route('**/api/bots',async route=>{
    if(route.request().method()==='PUT') {
      const body=route.request().postDataJSON();writes.push(body);grok.config=body;grok.images=body.share_images;
    }
    return route.fulfill({json:status});
  });
  await page.route('**/api/bots/deliveries',route=>route.fulfill({json:{deliveries:[]}}));
  await page.goto('/#agent');
  await openDetails(page, '#bot-tools');
  await expect(page.locator('#bot-images')).not.toBeChecked();
  await expect(page.locator('#bot-images')).toBeDisabled();
  await expect(page.locator('#bot-image-test')).toBeDisabled();
  await expect(page.locator('#bot-fields')).toContainText('All Bots on your Grok account');
  grok.image_owner_allowed=true;grok.image_transport_ready=true;
  await page.reload();
  await openDetails(page, '#bot-tools');
  await expect(page.locator('#bot-images')).toBeEnabled();
  await page.locator('#bot-images').check();
  let accept=true,dialogs=[];
  page.on('dialog',async dialog=>{dialogs.push(dialog.message());await(accept?dialog.accept():dialog.dismiss());});
  await page.getByRole('button',{name:'Save bot setup',exact:true}).click();
  await expect(page.locator('#bot-image-state')).toContainText('One camera image per ring');
  expect(writes).toHaveLength(1);
  expect(writes[0].share_images).toBe(true);
  expect(writes[0].image_consent).toBeUndefined();
  expect(writes[0].share_names).toBeUndefined();
  await expect(page.locator('#privacy-sharing')).toContainText('One camera image per ring');
  let probes=[];
  await page.route('**/api/bots/grok/test-image',route=>{
    probes.push(route.request().postDataJSON());
    return route.fulfill({status:202,json:{image_bytes:1200,camera_used:false}});
  });
  accept=false;
  await page.locator('#bot-image-test').click();
  expect(probes).toHaveLength(0);
  accept=true;
  await page.locator('#bot-image-test').click();
  await expect.poll(()=>probes.length).toBe(1);
  expect(Object.keys(probes[0])).toEqual(['request_id']);
  expect(probes[0].request_id).toMatch(/^[0-9a-f]{32}$/);
  expect(dialogs.at(-1)).toContain('camera-scene quality still needs verification');
});

test('chosen-photo enrollment requires consent and supports local deletion', async ({page})=>{
  let count=0,uploads=0;
  await page.route('**/api/profiles**',async route=>{
    const req=route.request(),url=new URL(req.url());
    if(url.pathname==='/api/profiles' && req.method()==='GET')return route.fulfill({json:{profiles:[{id:'fixture-person',name:'Local fixture',note:'Synthetic UI fixture',face_samples:count}],vision:{available:true}}});
    if(url.pathname.endsWith('/enroll')) {expect(url.searchParams.get('consent')).toBeNull();expect(req.postDataBuffer().length).toBeGreaterThan(0);uploads++;count++;return route.fulfill({status:201,json:{stored:'local'}});}
    if(url.pathname.endsWith('/enrollment')) {count=0;return route.fulfill({status:204});}
    return route.fallback();
  });
  page.on('dialog',d=>d.accept());
  await page.goto('/#people');
  await page.getByRole('button',{name:'Enroll photo',exact:true}).click();
  await page.locator('#enroll-photo').setInputFiles({name:'synthetic.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jr1kAAAAASUVORK5CYII=','base64')});
  await page.getByRole('button',{name:'Enroll locally',exact:true}).click();
  await expect(page.locator('#people-list')).toContainText('1 LOCAL PHOTOS');
  await expect(page.locator('#enroll-feedback')).toContainText('Photo accepted');
  await page.locator('#enroll-dialog').getByRole('button',{name:'Done',exact:true}).click();
  await page.getByRole('button',{name:'Delete enrolled photos',exact:true}).click();
  await expect(page.locator('#people-list')).toContainText('0 LOCAL PHOTOS');
  expect(uploads).toBe(1);
});

const chosenFixture={name:'synthetic-reference.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jr1kAAAAASUVORK5CYII=','base64')};
async function mockPeopleFlow(page) {
  const fixture={people:[],created:0,edited:0,uploads:0,tests:0,rejectPhoto:false,result:'probable_match',releaseUpload:null};
  await page.route('**/api/profiles**',async route=>{
    const req=route.request(),url=new URL(req.url()),id=url.pathname.split('/')[3];
    if(url.pathname==='/api/profiles' && req.method()==='GET')return route.fulfill({json:{profiles:fixture.people,vision:{available:true}}});
    if(url.pathname==='/api/profiles' && req.method()==='POST') {
      fixture.created++;const person={...req.postDataJSON(),id:'synthetic-person-'+fixture.created,face_samples:0};fixture.people.push(person);
      return route.fulfill({status:201,json:person});
    }
    const person=fixture.people.find(p=>p.id===id);
    if(req.method()==='PUT') {fixture.edited++;Object.assign(person,req.postDataJSON());return route.fulfill({json:person});}
    if(url.pathname.endsWith('/enroll')) {
      fixture.uploads++;expect(url.searchParams.get('consent')).toBeNull();
      if(fixture.releaseUpload) await fixture.releaseUpload;
      if(fixture.rejectPhoto){fixture.rejectPhoto=false;return route.fulfill({status:422,json:{detail:'Face is too small or blurry. Choose a sharper, closer photo.'}});}
      person.face_samples++;
      return route.fulfill({status:201,json:{stored:'local',face_samples:person.face_samples,quality:{status:'usable'}}});
    }
    if(url.pathname.endsWith('/enrollment')) {person.face_samples=0;return route.fulfill({status:204});}
    if(req.method()==='DELETE'){fixture.people=fixture.people.filter(p=>p.id!==id);return route.fulfill({status:204});}
    return route.fallback();
  });
  await page.route('**/api/recognition/test**',route=>{
    fixture.tests++;expect(new URL(route.request().url()).searchParams.get('consent')).toBeNull();
    expect(route.request().postDataBuffer().length).toBeGreaterThan(0);
    const probable=fixture.result==='probable_match';
    return route.fulfill({json:{status:fixture.result,profile_id:probable?fixture.people[0].id:null,person_name:probable?fixture.people[0].name:null,similarity:probable?.7:null,stored:false,camera_used:false,bot_sent:false}});
  });
  return fixture;
}

test('Add people setup saves once, supports back and cancel, and tests only a chosen photo',async ({page})=>{
  const fixture=await mockPeopleFlow(page);
  await page.goto('/');
  await page.locator('#setup-add-people').click();
  await page.locator('#person-name').fill('Avery fixture');
  await page.getByRole('button',{name:'Cancel',exact:true}).click();
  expect(fixture.created).toBe(0);
  await page.locator('#add-person').click();
  await page.locator('#person-name').fill('Avery fixture');
  await page.locator('#person-next').click();
  await expect(page.locator('#enroll-dialog')).toBeVisible();
  await page.locator('#enroll-back').click();
  await expect(page.locator('#person-name')).toHaveValue('Avery fixture');
  await page.locator('#person-name').fill('Avery renamed');
  await page.locator('#person-next').click();
  await expect(page.locator('#enroll-title')).toContainText('Avery renamed');
  expect(fixture.created).toBe(1);expect(fixture.edited).toBe(1);expect(fixture.uploads).toBe(0);
  await page.locator('#enroll-photo').setInputFiles(chosenFixture);
  await page.locator('#enroll-save').click();
  await expect(page.locator('#enroll-feedback')).toContainText('Photo accepted');
  await expect(page.locator('#enroll-photo')).toHaveValue('');
  await page.locator('#enroll-next').click();
  expect(fixture.tests).toBe(0);
  await page.locator('#recognition-photo').setInputFiles(chosenFixture);
  await page.locator('#recognition-run').click();
  await expect(page.locator('#recognition-result')).toContainText('Probably Avery renamed (0.70)');
  await expect(page.locator('#recognition-photo')).toHaveValue('');
  await page.locator('#recognition-back').click();
  await expect(page.locator('#enroll-count')).toContainText('1 of 5');
  await page.locator('#enroll-dialog').getByRole('button',{name:'Done',exact:true}).click();
  await expect(page.locator('#people-list')).toContainText('Avery renamed');
  expect(fixture.uploads).toBe(1);expect(fixture.tests).toBe(1);
});

test('enrollment quality failures are actionable and repeated submits do not duplicate uploads',async ({page})=>{
  const fixture=await mockPeopleFlow(page);fixture.people.push({id:'synthetic-person-1',name:'Avery fixture',note:'',face_samples:0});fixture.rejectPhoto=true;
  await page.goto('/#people');
  await page.getByRole('button',{name:'Enroll photo',exact:true}).click();
  await page.locator('#enroll-photo').setInputFiles(chosenFixture);
  await page.locator('#enroll-save').click();
  await expect(page.locator('#enroll-error')).toContainText('sharper, closer photo');
  await expect(page.locator('#enroll-next')).toBeDisabled();
  let release;fixture.releaseUpload=new Promise(resolve=>{release=resolve;});
  await page.locator('#enroll-save').click();
  await expect(page.locator('#enroll-save')).toBeDisabled();
  await page.locator('#enroll-form').dispatchEvent('submit');
  await expect(page.locator('#enroll-back')).toBeDisabled();
  await expect(page.getByRole('button',{name:'Close enrollment',exact:true})).toBeDisabled();
  release();
  await expect(page.locator('#enroll-count')).toContainText('1 of 5');
  expect(fixture.uploads).toBe(2);
  await page.locator('#enroll-next').click();
  fixture.result='uncertain';
  await page.locator('#recognition-photo').setInputFiles(chosenFixture);
  await page.locator('#recognition-run').click();
  await expect(page.locator('#recognition-result')).toContainText('unknown');
  await expect(page.locator('#recognition-result')).not.toContainText('Avery fixture');
});

test('iPhone-sized enrollment supports preview, cancel, format guidance and local management',async ({page})=>{
  const fixture=await mockPeopleFlow(page);fixture.people.push({id:'synthetic-person-1',name:'Avery fixture',note:'Synthetic UI only',face_samples:1});
  await page.setViewportSize({width:390,height:844});
  let confirmDelete=false;page.on('dialog',dialog=>confirmDelete?dialog.accept():dialog.dismiss());
  await page.goto('/#people');
  await page.getByRole('button',{name:'Enroll photo',exact:true}).click();
  await page.locator('#enroll-photo').setInputFiles({name:'phone.heic',mimeType:'image/heic',buffer:Buffer.from('synthetic unsupported fixture')});
  await expect(page.locator('#enroll-error')).toContainText('export as JPEG/PNG');
  await page.locator('#enroll-photo').setInputFiles(chosenFixture);
  await expect(page.locator('#enroll-preview')).toBeVisible();
  await expect.poll(()=>page.locator('#enroll-preview-image').evaluate(image=>image.naturalWidth)).toBeGreaterThan(0);
  expect(await page.locator('#enroll-photo').getAttribute('capture')).toBeNull();
  const layout=await page.locator('#enroll-dialog').evaluate(dialog=>({left:dialog.getBoundingClientRect().left,right:dialog.getBoundingClientRect().right,font:getComputedStyle(dialog.querySelector('input[type=file]')).fontSize,scroll:document.documentElement.scrollWidth}));
  expect(layout.left).toBeGreaterThanOrEqual(0);expect(layout.right).toBeLessThanOrEqual(390);expect(layout.scroll).toBeLessThanOrEqual(390);expect(layout.font).toBe('16px');
  const button=await page.locator('#enroll-save').boundingBox();expect(button.height).toBeGreaterThanOrEqual(44);
  await page.screenshot({path:'/tmp/agent-doorbell-people-v034-mobile.png'});
  await page.getByRole('button',{name:'Close enrollment',exact:true}).click();
  expect(fixture.uploads).toBe(0);
  await page.getByRole('button',{name:'Delete enrolled photos',exact:true}).click();
  expect(fixture.people[0].face_samples).toBe(1);
  confirmDelete=true;
  await page.getByRole('button',{name:'Delete enrolled photos',exact:true}).click();
  await expect(page.locator('#people-list')).toContainText('0 LOCAL PHOTOS');
  await expect(page.locator('#recognition-open')).toBeDisabled();
  await page.getByRole('button',{name:'Remove Avery fixture',exact:true}).click();
  await expect(page.locator('#people-list')).toContainText('Make room for familiar people');
});

test('event details show positive objects and useful face quality without absent animals', async ({page,request})=>{
  const status=await (await request.get('/api/status')).json();
  status.local_vision={...status.local_vision,objects_available:true};
  const fixture={id:'object-observation-fixture',occurred_at:'2026-10-07T02:50:00+00:00',
    kind:'test',source:'browser',capture_status:'complete',frames:0,media:[],note:'',simulated:true,
    person_id:null,person_status:'detected',face_status:'low_quality',acknowledged:false,
    description:"A person and a backpack were detected. The face isn't clear enough to identify them.",description_status:'ready',description_source:'local_detections',
    local_analysis:{objects:[{label:'person'},{label:'backpack'}],timings_ms:{analysis_total:100}}};
  await page.route('**/api/status',route=>route.fulfill({json:status}));
  await page.route('**/api/events**',route=>{
    const path=new URL(route.request().url()).pathname;
    if(path==='/api/events')return route.fulfill({json:{events:[fixture]}});
    if(path==='/api/events/'+fixture.id)return route.fulfill({json:fixture});
    return route.fallback();
  });
  await page.goto('/');
  await expect(page.locator('#connection')).toContainText('Live updates connected');
  await expect(page.locator('#glance-person')).toHaveText('Object detector ready');
  await expect(page.locator('#recent-events')).toContainText('Local observations: A person and a backpack were detected.');
  await page.getByRole('button',{name:/^Activity/}).click();
  await page.locator('#all-events .event-row').first().click();
  await expect(page.locator('#event-meta')).toContainText('Face too small or blurry');
  await openDetails(page, '#event-recognition');
  await expect(page.locator('#event-meta')).toContainText('Detected: person, backpack');
  await expect(page.locator('#event-meta')).toContainText(fixture.description);
  await expect(page.locator('#event-meta')).not.toContainText('dog');
  await page.getByRole('button',{name:'Close event',exact:true}).click();
  fixture.local_analysis.objects=[];
  fixture.person_status='not_detected';fixture.face_status='no_face';
  fixture.description=null;fixture.description_status='no_confident_observations';
  await page.locator('#all-events .event-row').first().click();
  await openDetails(page, '#event-recognition');
  await expect(page.locator('#event-meta')).toContainText('Local analysis: 100 ms');
  await expect(page.locator('#event-meta')).not.toContainText('Detected:');
  await expect(page.locator('#event-meta')).not.toContainText('dog');
  await expect(page.locator('#event-meta')).toContainText('does not establish that the scene is empty');
});

test('burst diagnostics explain disagreements without displaying candidate identity data', async ({page})=>{
  const fixture={id:'burst-diagnostics-fixture',occurred_at:'2026-10-07T12:00:00+00:00',
    kind:'test',source:'browser',capture_status:'complete',frames:0,media:[],note:'',simulated:true,
    person_id:null,person_status:'detected',face_status:'uncertain',acknowledged:false,
    description:'A person was detected. The face match is uncertain.',description_status:'ready',
    local_analysis:{objects:[{label:'person'}],timings_ms:{analysis_total:140},expected_frames:3,complete:true,
      aggregation:{reason:'identity_disagreement'},frames:[
        {index:0,status:'no_face'},
        {index:1,status:'probable_match',profile_id:'PRIVATE_CANDIDATE_A',embedding:'PRIVATE_VECTOR'},
        {index:2,status:'probable_match',profile_id:'PRIVATE_CANDIDATE_B',name:'PRIVATE_NAME'}]}};
  await page.route('**/api/events**',route=>{
    const path=new URL(route.request().url()).pathname;
    if(path==='/api/events')return route.fulfill({json:{events:[fixture]}});
    if(path==='/api/events/'+fixture.id)return route.fulfill({json:fixture});
    return route.fallback();
  });
  await page.goto('/');
  await expect(page.locator('#connection')).toContainText('Live updates connected');
  await page.getByRole('button',{name:/^Activity/}).click();
  await page.locator('#all-events .event-row').first().click();
  await openDetails(page, '#event-recognition');
  await expect(page.locator('#event-meta')).toContainText('Recognition checked 3 of 3 frames.');
  await expect(page.locator('#event-meta')).toContainText('Frame 1: No clear face');
  await expect(page.locator('#event-meta')).toContainText('Frame 2: Probable enrolled match');
  await expect(page.locator('#event-meta')).toContainText('Different people matched across frames. No single identity was assigned.');
  await expect(page.locator('#event-meta')).not.toContainText('PRIVATE_');
});

test('local caption is separate untrusted text and timeout keeps quick observations',async ({page,request})=>{
  const status=await (await request.get('/api/status')).json();
  status.local_captions={enabled:true,active:true,provider:{available:true}};
  const fixture={id:'local-caption-fixture',occurred_at:'2026-10-07T02:50:00+00:00',kind:'test',source:'browser',capture_status:'complete',frames:0,media:[],note:'',simulated:true,
    person_id:null,person_status:'detected',face_status:'low_quality',acknowledged:false,description:'A person was detected.',description_status:'ready',local_analysis:null,caption:null,caption_status:'running',caption_error:null};
  await page.addInitScript(()=>{window.captionPwned=false;});
  await page.route('**/api/status',route=>route.fulfill({json:status}));
  await page.route('**/api/events**',route=>{
    const path=new URL(route.request().url()).pathname;
    if(path==='/api/events')return route.fulfill({json:{events:[fixture]}});
    if(path==='/api/events/'+fixture.id)return route.fulfill({json:fixture});
    return route.fallback();
  });
  await page.goto('/#history');
  await page.locator('#all-events .event-row').first().click();
  await expect(page.locator('#event-meta')).toContainText('Local observations: A person was detected.');
  await expect(page.locator('#event-meta')).toContainText('processing separately');
  await page.getByRole('button',{name:'Close event',exact:true}).click();
  fixture.caption='<img src=x onerror="window.captionPwned=true"> A bag appears near the door.';fixture.caption_status='ready';status.local_captions.active=false;
  await page.reload();
  await expect(page.locator('#all-events')).toContainText('Local model (unverified)');
  await page.locator('#all-events .event-row').first().click();
  await expect(page.locator('#event-meta')).toContainText(fixture.caption);
  await expect(page.locator('#event-meta img')).toHaveCount(0);
  expect(await page.evaluate(()=>window.captionPwned)).toBe(false);
  await page.getByRole('button',{name:'Close event',exact:true}).click();
  fixture.caption=null;fixture.caption_status='timeout';fixture.caption_error='Caption deadline reached; owned model process stopped.';
  await page.locator('#all-events .event-row').first().click();
  await expect(page.locator('#event-meta')).toContainText('owned model process stopped');
  await expect(page.locator('#event-meta')).toContainText('Local observations: A person was detected.');
});

test('simulated commands require exact review and opening is separate from unlocking',async ({page})=>{
  await page.addInitScript(()=>{Object.defineProperty(crypto,'randomUUID',{value:undefined});});
  await page.goto('/#agent');
  const panel=page.getByRole('region',{name:'Access simulation'});
  await expect(panel).toContainText('No physical lock is connected');
  await expect(panel).toContainText('A simulated open stays on this page');
  await expect(page.locator('#access-lock-state')).toContainText('Actual lock: unknown');
  await openDetails(page, '#lock-commands');
  await page.locator('#access-command').selectOption('unlock');
  await page.getByRole('button',{name:'Propose simulated command',exact:true}).click();
  const unlock=page.locator('#access-command-list article').filter({hasText:'Simulated unlock · pending'}).first();
  await expect(unlock).toBeVisible();
  page.on('dialog',dialog=>dialog.accept());
  await unlock.getByRole('button',{name:'Approve simulated command',exact:true}).click();
  await expect(page.locator('#access-command-list')).toContainText('Simulation completed. No physical lock action occurred.');
  await expect(page.locator('#access-lock-state')).toContainText('Simulated lock: unlocked');
  await page.locator('#access-command').selectOption('open');
  await page.getByRole('button',{name:'Propose simulated command',exact:true}).click();
  const opening=page.locator('#access-command-list article').filter({hasText:'Simulated open · pending'}).first();
  await opening.getByRole('button',{name:'Approve simulated command',exact:true}).click();
  await expect(page.locator('#access-command-list')).toContainText('Opening a door is a separate capability');
  await expect(page.locator('#access-lock-state')).toContainText('Physical door position: unknown');
});

test('advance visitor approval allows one valid simulated arrival without repeating owner approval',async ({page,request})=>{
  const before=(await (await request.get('/api/profiles')).json()).profiles;
  let decisions=0;
  page.on('dialog',dialog=>{decisions+=1;dialog.accept();});
  await page.goto('/#agent');
  await expect(page.locator('#access-valid-from')).not.toHaveValue('');
  await page.locator('#access-visitor-name').fill('Avery visitor fixture');
  await page.getByRole('button',{name:'Review simulated visitor pass',exact:true}).click();
  const card=page.locator('#access-pass-list article').filter({hasText:'Avery visitor fixture'}).first();
  await expect(card).toContainText('5 pretend references');
  await card.getByRole('button',{name:'Approve simulated pass',exact:true}).click();
  await expect(card).toContainText('approved');
  await card.getByRole('button',{name:'Simulate arrival',exact:true}).click();
  await expect(page.locator('#access-arrival-dialog')).toContainText('No repeat owner approval at arrival');
  await page.locator('#access-arrival-run').click();
  await expect(page.locator('#access-arrival-result')).toContainText('Visitor credential missing');
  await page.locator('#access-arrival-factor').check();
  await page.locator('#access-arrival-run').click();
  await expect(page.locator('#access-arrival-result')).toContainText('Simulation completed. No physical lock action occurred.');
  await expect(page.locator('#access-arrival-run')).toBeDisabled();
  await expect(card).toContainText('Used in simulation');
  expect(decisions).toBe(1);
  expect((await (await request.get('/api/profiles')).json()).profiles).toEqual(before);
  const snapshot=await (await request.get('/api/access/simulation')).json();
  expect(snapshot.status.real_actuation_enabled).toBe(false);
  expect(snapshot.status.grok_return_transport).toBe('unconfigured');
});

test('mobile access simulation renders visitor names as text and revocation removes arrival action',async ({page,request})=>{
  await page.setViewportSize({width:390,height:844});
  const now=new Date();
  const response=await request.post('/api/access/simulation/passes',{data:{simulation_only:true,
    request_id:'12345678901234567890123456789012',visitor_name:'<img src=x onerror=alert(1)> fixture',
    mock_reference_count:5,valid_from:now.toISOString(),valid_until:new Date(now.getTime()+3600000).toISOString()}});
  const item=await response.json();
  expect(response.ok()).toBe(true);
  await request.post(`/api/access/simulation/passes/${item.id}/decision`,{data:{simulation_only:true,
    payload_digest:item.digest,confirm_mock_owner:true,decision:'approve'}});
  await page.goto('/#agent');
  const card=page.locator('#access-pass-list article').filter({hasText:'<img src=x onerror=alert(1)> fixture'});
  await expect(card).toContainText('approved');
  await expect(card.locator('img')).toHaveCount(0);
  page.on('dialog',dialog=>dialog.accept());
  await card.getByRole('button',{name:'Revoke simulated pass',exact:true}).click();
  await expect(card).toContainText('revoked');
  await expect(card.getByRole('button',{name:'Simulate arrival',exact:true})).toHaveCount(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  expect(await page.locator('#access-visitor-name').evaluate(e=>getComputedStyle(e).fontSize)).toBe('16px');
});

test('five-photo proposal is reviewed before a one-use pass and needs no arrival approval', async ({ page }) => {
  await page.goto('/#agent');
  await expect(page.locator('#access-valid-from')).not.toHaveValue('');
  await page.locator('#access-visitor-name').fill('Five photo visitor');
  await openDetails(page, '#bridge-panel');
  await page.locator('#bridge-fixture-create').click();
  const proposal = page.locator('#bridge-proposal-list article').filter({hasText:'Five photo visitor'});
  await expect(proposal).toContainText('pending');
  await expect(proposal).toContainText('five generated images');
  await expect(page.locator('#access-pass-list article').filter({hasText:'Five photo visitor'})).toHaveCount(0);
  let approvals=0;
  page.on('dialog', async dialog => {approvals++;expect(dialog.message()).toContain('five generated demo images');await dialog.accept();});
  await proposal.getByRole('button',{name:'Approve demo references and pass'}).click();
  await expect(proposal).toContainText('approved');
  const pass = page.locator('#access-pass-list article').filter({hasText:'Five photo visitor'});
  await expect(pass).toContainText('approved');
  await pass.getByRole('button',{name:'Simulate arrival'}).click();
  await page.locator('#access-arrival-factor').check();
  await page.locator('#access-arrival-run').click();
  await expect(page.locator('#access-arrival-result')).toContainText('Simulation completed');
  expect(approvals).toBe(1);
  await expect(pass).toContainText('used');
  await expect(page.locator('#bridge-status')).toContainText('awaits owner setup');
});
