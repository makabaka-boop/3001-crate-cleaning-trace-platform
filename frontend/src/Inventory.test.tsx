import {render,screen,waitFor,fireEvent,within} from '@testing-library/react'; import {vi,test,expect} from 'vitest'; import App from './App';
const dash={total_crates:4,active_crates:4,clean_crates:1,pending_issues:0,isolated_crates:0};
const ok=(data:unknown)=>({ok:true,json:()=>Promise.resolve(data)});
type Chk={id:number;location:string;status:string;created_at:string;completed_at:string|null;expected:string[];scanned:string[];missing:string[];misplaced:{crate_code:string;location:string}[]};
const mkCrate=(id:number,code:string,location:string,backup:string|null=null)=>({id,code,name:`箱${code}`,location,cleaning_status:'dirty',active:true,isolated:false,backup_code:backup});
// 模拟后端盘点契约：创建时按在用箱体快照，完成时解析主/备编号并结算缺失与错放
const setup=(crates:any[],preset:Chk[]=[])=>{
 const checks:Chk[]=[...preset];const bodies:any[]=[];let nextId=100;
 const resolve=(code:string)=>crates.find(c=>c.code===code||c.backup_code===code);
 const fetchImpl=(url:string,opts?:RequestInit)=>{const u=String(url);
  if(u.includes('/inventory-checks')&&u.includes('/complete')&&opts?.method==='POST'){
   const b=JSON.parse(String(opts.body));bodies.push(b);
   const chk=checks.find(c=>c.id===Number(u.match(/inventory-checks\/(\d+)/)?.[1]))!;
   const unknown=b.scanned_codes.find((x:string)=>!resolve(x));
   if(unknown)return Promise.resolve({ok:false,status:404,json:()=>Promise.resolve({detail:`周转箱编号不存在: ${unknown}`})});
   const scanned=[...new Set(b.scanned_codes.map((x:string)=>resolve(x).code))] as string[];
   chk.status='completed';chk.completed_at='2026-09-12T10:00:00';
   chk.scanned=[...scanned].sort();
   chk.missing=chk.expected.filter(c=>!scanned.includes(c));
   chk.misplaced=scanned.filter(c=>!chk.expected.includes(c)).map(c=>({crate_code:c,location:resolve(c).location}));
   return Promise.resolve(ok(JSON.parse(JSON.stringify(chk))));
  }
  if(u.endsWith('/api/inventory-checks')&&opts?.method==='POST'){
   const b=JSON.parse(String(opts.body));
   const expected=crates.filter(c=>c.active&&c.location===b.location).map(c=>c.code).sort();
   if(!expected.length)return Promise.resolve({ok:false,status:404,json:()=>Promise.resolve({detail:`库位无在用周转箱，无法创建盘点: ${b.location}`})});
   const chk:Chk={id:nextId++,location:b.location,status:'in_progress',created_at:'2026-09-12T08:00:00',completed_at:null,expected,scanned:[],missing:[],misplaced:[]};
   checks.unshift(chk);return Promise.resolve(ok(JSON.parse(JSON.stringify(chk))));
  }
  if(u.includes('/inventory-checks'))return Promise.resolve(ok(checks.map(c=>({...c}))));
  if(u.includes('dashboard'))return Promise.resolve(ok(dash));
  if(u.includes('/crates'))return Promise.resolve(ok(crates));
  return Promise.resolve(ok([]));
 };
 return {checks,bodies,fetchImpl};
};
const openInventory=async(fetchImpl:(url:string,opts?:RequestInit)=>Promise<unknown>)=>{
 global.fetch=vi.fn(fetchImpl) as any;
 render(<App/>);
 await waitFor(()=>expect(screen.getByText('周转箱总数')).toBeInTheDocument());
 fireEvent.click(screen.getByRole('button',{name:'库位盘点'}));
 await waitFor(()=>expect(screen.getByRole('option',{name:'仓库'})).toBeInTheDocument());
};
const scan=(code:string)=>{fireEvent.change(screen.getByLabelText(/扫描箱号/),{target:{value:code}});fireEvent.click(screen.getByRole('button',{name:'添加箱号'}))};
const grp=(panel:HTMLElement,t:string)=>within(panel).getByText(t).closest('section') as HTMLElement;

test('start check, scan and submit: result distinguishes missing and misplaced, kept for review',async()=>{
 const crates=[mkCrate(1,'BX-1','仓库'),mkCrate(2,'BX-2','仓库'),mkCrate(3,'BX-3','仓库'),mkCrate(9,'BX-9','清洗区')];
 const {bodies,fetchImpl}=setup(crates);
 await openInventory(fetchImpl);
 fireEvent.change(screen.getByLabelText('盘点库位'),{target:{value:'仓库'}});
 fireEvent.click(screen.getByRole('button',{name:'开始盘点'}));
 await waitFor(()=>expect(screen.getByText(/盘点单创建成功：库位 仓库 应在 3 箱/)).toBeInTheDocument());
 // 创建时的在用箱体快照作为核对基准展示
 const active=screen.getByText(/盘点中 · 库位 仓库 · 应在 3 箱/).closest('section') as HTMLElement;
 ['BX-1','BX-2','BX-3'].forEach(c=>expect(within(active).getByText(c)).toBeInTheDocument());
 // 逐个扫描：BX-1、BX-2 在本库位，BX-9 属于清洗区
 ['BX-1','BX-2','BX-9'].forEach(scan);
 fireEvent.click(screen.getByRole('button',{name:'提交盘点（3箱）'}));
 await waitFor(()=>expect(screen.getByText('盘点完成：应在 3 箱，实扫 3 箱，缺失 1 箱，错放 1 箱')).toBeInTheDocument());
 expect(bodies).toEqual([{scanned_codes:['BX-1','BX-2','BX-9']}]);
 // 结果明细：应在、实扫、缺失、错放各自成组
 const result=screen.getByText(/盘点结果 · 库位 仓库/).closest('section') as HTMLElement;
 ['BX-1','BX-2','BX-3'].forEach(c=>expect(within(grp(result,'应在（3）')).getByText(c)).toBeInTheDocument());
 ['BX-1','BX-2','BX-9'].forEach(c=>expect(within(grp(result,'实扫（3）')).getByText(c)).toBeInTheDocument());
 expect(within(grp(result,'缺失（1）')).getByText('BX-3')).toBeInTheDocument();
 expect(within(grp(result,'错放（1）')).getByText('BX-9')).toBeInTheDocument();
 expect(within(grp(result,'错放（1）')).getByText(/登记位置：清洗区/)).toBeInTheDocument();
 // 已完成盘点保留在记录中供当次结果复看
 const row=screen.getByText('#100').closest('tr') as HTMLElement;
 expect(within(row).getByText('已完成')).toBeInTheDocument();
 expect(within(row).getByText('3/3/1/1')).toBeInTheDocument();
 fireEvent.click(within(row).getByRole('button',{name:'复看结果'}));
 expect(screen.getByText(/盘点结果 · 库位 仓库/)).toBeInTheDocument();
});

test('scanning a backup code counts toward its primary crate',async()=>{
 const crates=[mkCrate(1,'BX-1','仓库','BX-1-NEW'),mkCrate(2,'BX-2','仓库')];
 const {bodies,fetchImpl}=setup(crates);
 await openInventory(fetchImpl);
 fireEvent.change(screen.getByLabelText('盘点库位'),{target:{value:'仓库'}});
 fireEvent.click(screen.getByRole('button',{name:'开始盘点'}));
 await waitFor(()=>expect(screen.getByText(/盘点单创建成功/)).toBeInTheDocument());
 ['BX-1-NEW','BX-2'].forEach(scan);
 fireEvent.click(screen.getByRole('button',{name:'提交盘点（2箱）'}));
 await waitFor(()=>expect(screen.getByText('盘点完成：应在 2 箱，实扫 2 箱，缺失 0 箱，错放 0 箱')).toBeInTheDocument());
 // 提交备用编号，结果归入主编号
 expect(bodies).toEqual([{scanned_codes:['BX-1-NEW','BX-2']}]);
 const result=screen.getByText(/盘点结果 · 库位 仓库/).closest('section') as HTMLElement;
 expect(within(grp(result,'实扫（2）')).getByText('BX-1')).toBeInTheDocument();
 expect(within(grp(result,'实扫（2）')).getByText('BX-2')).toBeInTheDocument();
 expect(screen.getByText('缺失（0）')).toBeInTheDocument();
 expect(screen.getByText('错放（0）')).toBeInTheDocument();
});

test('unknown code keeps unsubmitted scans and allows fix and retry',async()=>{
 const crates=[mkCrate(1,'BX-1','仓库'),mkCrate(2,'BX-2','仓库')];
 const {bodies,fetchImpl}=setup(crates);
 await openInventory(fetchImpl);
 fireEvent.change(screen.getByLabelText('盘点库位'),{target:{value:'仓库'}});
 fireEvent.click(screen.getByRole('button',{name:'开始盘点'}));
 await waitFor(()=>expect(screen.getByText(/盘点单创建成功/)).toBeInTheDocument());
 ['BX-1','BX-NONE'].forEach(scan);
 fireEvent.click(screen.getByRole('button',{name:'提交盘点（2箱）'}));
 await waitFor(()=>expect(screen.getByText('周转箱编号不存在: BX-NONE')).toBeInTheDocument());
 // 盘点未完成，未提交的扫描内容保留
 expect(screen.getByText(/盘点中 · 库位 仓库/)).toBeInTheDocument();
 expect(screen.getByText('BX-NONE')).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'提交盘点（2箱）'})).toBeInTheDocument();
 // 移除错误箱号后重试成功
 fireEvent.click(within(screen.getByText('BX-NONE')).getByTitle('移除'));
 fireEvent.click(screen.getByRole('button',{name:'提交盘点（1箱）'}));
 await waitFor(()=>expect(screen.getByText('盘点完成：应在 2 箱，实扫 1 箱，缺失 1 箱，错放 0 箱')).toBeInTheDocument());
 expect(bodies).toHaveLength(2);
 expect(bodies[1]).toEqual({scanned_codes:['BX-1']});
 const result=screen.getByText(/盘点结果 · 库位 仓库/).closest('section') as HTMLElement;
 expect(within(grp(result,'缺失（1）')).getByText('BX-2')).toBeInTheDocument();
});

test('duplicate scanned codes warn and block submit',async()=>{
 const crates=[mkCrate(1,'BX-1','仓库'),mkCrate(2,'BX-2','仓库')];
 const {bodies,fetchImpl}=setup(crates);
 await openInventory(fetchImpl);
 fireEvent.change(screen.getByLabelText('盘点库位'),{target:{value:'仓库'}});
 fireEvent.click(screen.getByRole('button',{name:'开始盘点'}));
 await waitFor(()=>expect(screen.getByText(/盘点单创建成功/)).toBeInTheDocument());
 ['BX-1','BX-1'].forEach(scan);
 expect(screen.getByText(/重复箱号：BX-1/)).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'提交盘点（2箱）'}));
 await waitFor(()=>expect(screen.getByText(/存在重复箱号/)).toBeInTheDocument());
 expect(bodies).toHaveLength(0);
 expect(screen.getByText(/盘点中 · 库位 仓库/)).toBeInTheDocument();
});

test('empty location shows locatable feedback and does not start a check',async()=>{
 const crates=[mkCrate(1,'BX-1','仓库')];
 global.fetch=vi.fn((url,opts)=>{const u=String(url);
  if(u.endsWith('/api/inventory-checks')&&opts?.method==='POST')return Promise.resolve({ok:false,status:404,json:()=>Promise.resolve({detail:'库位无在用周转箱，无法创建盘点: 仓库'})});
  if(u.includes('/inventory-checks'))return Promise.resolve(ok([]));
  if(u.includes('dashboard'))return Promise.resolve(ok(dash));
  if(u.includes('/crates'))return Promise.resolve(ok(crates));
  return Promise.resolve(ok([]))}) as any;
 render(<App/>);
 await waitFor(()=>expect(screen.getByText('周转箱总数')).toBeInTheDocument());
 fireEvent.click(screen.getByRole('button',{name:'库位盘点'}));
 await waitFor(()=>expect(screen.getByRole('option',{name:'仓库'})).toBeInTheDocument());
 fireEvent.change(screen.getByLabelText('盘点库位'),{target:{value:'仓库'}});
 fireEvent.click(screen.getByRole('button',{name:'开始盘点'}));
 await waitFor(()=>expect(screen.getByText('库位无在用周转箱，无法创建盘点: 仓库')).toBeInTheDocument());
 expect(screen.queryByText(/盘点中 · 库位/)).not.toBeInTheDocument();
});

test('history keeps completed checks for review and in-progress check can resume',async()=>{
 const crates=[mkCrate(1,'BX-1','仓库'),mkCrate(2,'BX-2','仓库')];
 const done:Chk={id:7,location:'仓库',status:'completed',created_at:'2026-09-11T08:00:00',completed_at:'2026-09-11T09:00:00',expected:['BX-1','BX-2'],scanned:['BX-1'],missing:['BX-2'],misplaced:[]};
 const doing:Chk={id:8,location:'仓库',status:'in_progress',created_at:'2026-09-12T08:00:00',completed_at:null,expected:['BX-1','BX-2'],scanned:[],missing:[],misplaced:[]};
 const {fetchImpl}=setup(crates,[doing,done]);
 await openInventory(fetchImpl);
 // 已完成盘点可复看当次结果
 const doneRow=screen.getByText('#7').closest('tr') as HTMLElement;
 expect(within(doneRow).getByText('已完成')).toBeInTheDocument();
 fireEvent.click(within(doneRow).getByRole('button',{name:'复看结果'}));
 const result=screen.getByText(/盘点结果 · 库位 仓库/).closest('section') as HTMLElement;
 expect(within(grp(result,'应在（2）')).getByText('BX-2')).toBeInTheDocument();
 expect(within(grp(result,'实扫（1）')).getByText('BX-1')).toBeInTheDocument();
 expect(within(grp(result,'缺失（1）')).getByText('BX-2')).toBeInTheDocument();
 expect(within(grp(result,'错放（0）')).queryByText('BX-1')).not.toBeInTheDocument();
 // 进行中的盘点单可继续
 const doingRow=screen.getByText('#8').closest('tr') as HTMLElement;
 expect(within(doingRow).getByText('进行中')).toBeInTheDocument();
 fireEvent.click(within(doingRow).getByRole('button',{name:'继续盘点'}));
 await waitFor(()=>expect(screen.getByText(/继续盘点单 #8：库位 仓库 应在 2 箱/)).toBeInTheDocument());
 expect(screen.getByText(/盘点中 · 库位 仓库 · 应在 2 箱/)).toBeInTheDocument();
});
