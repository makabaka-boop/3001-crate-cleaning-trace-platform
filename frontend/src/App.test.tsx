import {render,screen,waitFor,fireEvent} from '@testing-library/react'; import {vi,test,expect} from 'vitest'; import App from './App';
const dash={total_crates:3,active_crates:2,clean_crates:1,pending_issues:2,isolated_crates:1};
test('renders dashboard metrics after loading',async()=>{global.fetch=vi.fn((url)=>Promise.resolve({ok:true,json:()=>Promise.resolve(String(url).includes('dashboard')?dash:[])})) as any;render(<App/>);await waitFor(()=>expect(screen.getByText('周转箱总数')).toBeInTheDocument());expect(screen.getByRole('heading',{name:/让每一个周转箱/})).toBeInTheDocument()});
test('shows API failure feedback',async()=>{global.fetch=vi.fn(()=>Promise.resolve({ok:false,status:500,json:()=>Promise.resolve({detail:'服务异常'})})) as any;render(<App/>);await waitFor(()=>expect(screen.getByText('服务异常')).toBeInTheDocument())});
const openBatch=async()=>{render(<App/>);await waitFor(()=>expect(screen.getByText('周转箱总数')).toBeInTheDocument());fireEvent.click(screen.getByRole('button',{name:'登记流转'}));fireEvent.click(screen.getByRole('button',{name:'批量模式'}))};
const fillBatchForm=()=>{fireEvent.change(screen.getByLabelText('批次编号'),{target:{value:'PCH-1'}});fireEvent.change(screen.getByLabelText('发生时间'),{target:{value:'2026-09-10T08:30'}});fireEvent.change(screen.getByLabelText('操作人'),{target:{value:'张师傅'}})};
test('batch mode registers events and shows generated event numbers in input order',async()=>{
 const bodies:any[]=[];
 global.fetch=vi.fn((url,opts)=>{const u=String(url);
  if(u.includes('/events/batch')){const b=JSON.parse(String(opts?.body));bodies.push(b);return Promise.resolve({ok:true,json:()=>Promise.resolve({batch_no:b.batch_no,results:b.crate_codes.map((c:string,i:number)=>({crate_code:c,event_no:`${b.batch_no}-${c}`,event:{id:i+1,event_no:`${b.batch_no}-${c}`,crate_id:i+1,event_type:b.event_type,occurred_at:b.occurred_at,operator:b.operator,description:''}}))})})}
  return Promise.resolve({ok:true,json:()=>Promise.resolve(u.includes('dashboard')?dash:[])})}) as any;
 await openBatch();fillBatchForm();
 fireEvent.change(screen.getByLabelText(/粘贴箱号/),{target:{value:'BX-001\nBX-002, BX-003'}});
 fireEvent.click(screen.getByRole('button',{name:'解析并添加'}));
 expect(screen.getByText('BX-001')).toBeInTheDocument();expect(screen.getByText('提交批量事件（3箱）')).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:/提交批量事件/}));
 await waitFor(()=>expect(screen.getByText(/批量登记成功/)).toBeInTheDocument());
 expect(bodies).toHaveLength(1);
 expect(bodies[0]).toMatchObject({batch_no:'PCH-1',event_type:'inbound',operator:'张师傅',crate_codes:['BX-001','BX-002','BX-003']});
 await waitFor(()=>expect(screen.getByText('PCH-1-BX-001')).toBeInTheDocument());
 expect(screen.getByText('PCH-1-BX-002')).toBeInTheDocument();expect(screen.getByText('PCH-1-BX-003')).toBeInTheDocument();
});
test('batch mode warns about duplicate codes and blocks submit',async()=>{
 const calls:string[]=[];
 global.fetch=vi.fn((url)=>{const u=String(url);if(u.includes('/events/batch'))calls.push(u);return Promise.resolve({ok:true,json:()=>Promise.resolve(u.includes('dashboard')?dash:[])})}) as any;
 await openBatch();fillBatchForm();
 fireEvent.change(screen.getByLabelText(/粘贴箱号/),{target:{value:'BX-001\nBX-001'}});
 fireEvent.click(screen.getByRole('button',{name:'解析并添加'}));
 expect(screen.getByText(/重复箱号：BX-001/)).toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:/提交批量事件/}));
 await waitFor(()=>expect(screen.getByText(/存在重复箱号/)).toBeInTheDocument());
 expect(calls).toHaveLength(0);
});
test('batch failure keeps form content for retry',async()=>{
 let fail=true;const bodies:any[]=[];
 global.fetch=vi.fn((url,opts)=>{const u=String(url);
  if(u.includes('/events/batch')){const b=JSON.parse(String(opts?.body));bodies.push(b);if(fail)return Promise.resolve({ok:false,status:404,json:()=>Promise.resolve({detail:'周转箱编号不存在: BX-404'})});return Promise.resolve({ok:true,json:()=>Promise.resolve({batch_no:b.batch_no,results:b.crate_codes.map((c:string)=>({crate_code:c,event_no:`${b.batch_no}-${c}`,event:{}}))})})}
  return Promise.resolve({ok:true,json:()=>Promise.resolve(u.includes('dashboard')?dash:[])})}) as any;
 await openBatch();fillBatchForm();
 fireEvent.change(screen.getByLabelText(/逐项录入箱号/),{target:{value:'BX-404'}});
 fireEvent.click(screen.getByRole('button',{name:'添加箱号'}));
 fireEvent.click(screen.getByRole('button',{name:/提交批量事件/}));
 await waitFor(()=>expect(screen.getByText('周转箱编号不存在: BX-404')).toBeInTheDocument());
 expect((screen.getByLabelText('批次编号') as HTMLInputElement).value).toBe('PCH-1');
 expect(screen.getByText('BX-404')).toBeInTheDocument();
 fail=false;
 fireEvent.click(screen.getByRole('button',{name:/提交批量事件/}));
 await waitFor(()=>expect(screen.getByText(/批量登记成功/)).toBeInTheDocument());
 expect(bodies).toHaveLength(2);expect(bodies[1].crate_codes).toEqual(['BX-404']);
});
