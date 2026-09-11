import {render,screen,waitFor,fireEvent} from '@testing-library/react'; import {vi,test,expect} from 'vitest'; import App from './App';
const dash={total_crates:1,active_crates:1,clean_crates:0,pending_issues:0,isolated_crates:0};
const crateList=(backup:string|null)=>[{id:1,code:'BX-1',name:'蓝色食品箱',location:'仓库',cleaning_status:'dirty',active:true,isolated:false,backup_code:backup}];
const ok=(data:unknown)=>({ok:true,json:()=>Promise.resolve(data)});
const openCrates=async(fetchImpl:(url:string,opts?:RequestInit)=>Promise<unknown>)=>{
 global.fetch=vi.fn(fetchImpl) as any;
 render(<App/>);
 await waitFor(()=>expect(screen.getByText('周转箱总数')).toBeInTheDocument());
 fireEvent.click(screen.getByRole('button',{name:'周转箱'}));
 await waitFor(()=>expect(screen.getByText('BX-1')).toBeInTheDocument());
};
test('bind backup code from ledger succeeds and card shows it',async()=>{
 let bound:string|null=null;const bodies:any[]=[];
 await openCrates((url,opts)=>{const u=String(url);
  if(u.includes('/backup-code')){const b=JSON.parse(String(opts?.body));bodies.push(b);bound=b.backup_code;return Promise.resolve(ok({...crateList(bound)[0]}))}
  return Promise.resolve(ok(u.includes('dashboard')?dash:u.includes('/crates')?crateList(bound):[]))});
 window.prompt=vi.fn(()=>'BX-1-NEW') as any;
 fireEvent.click(screen.getByRole('button',{name:'绑定备用编号'}));
 await waitFor(()=>expect(screen.getByText(/备用编号绑定成功/)).toBeInTheDocument());
 expect(bodies).toEqual([{backup_code:'BX-1-NEW'}]);
 await waitFor(()=>expect(screen.getByText('备用编号：BX-1-NEW')).toBeInTheDocument());
});
test('bind conflict shows occupied message and card stays unchanged',async()=>{
 await openCrates((url)=>{const u=String(url);
  if(u.includes('/backup-code'))return Promise.resolve({ok:false,status:409,json:()=>Promise.resolve({detail:'编号已被占用: BX-2'})});
  return Promise.resolve(ok(u.includes('dashboard')?dash:u.includes('/crates')?crateList(null):[]))});
 window.prompt=vi.fn(()=>'BX-2') as any;
 fireEvent.click(screen.getByRole('button',{name:'绑定备用编号'}));
 await waitFor(()=>expect(screen.getByText('编号已被占用: BX-2')).toBeInTheDocument());
 expect(screen.queryByText(/备用编号：/)).not.toBeInTheDocument();
});
test('crate without backup code renders no backup line',async()=>{
 await openCrates((url)=>Promise.resolve(ok(String(url).includes('dashboard')?dash:String(url).includes('/crates')?crateList(null):[])));
 expect(screen.queryByText(/备用编号：/)).not.toBeInTheDocument();
 expect(screen.getByRole('button',{name:'绑定备用编号'})).toBeInTheDocument();
});
