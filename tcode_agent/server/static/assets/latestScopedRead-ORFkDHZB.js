function a(t){let e=0;return{begin(){const n=++e,r=t();return()=>n===e&&r===t()},invalidate(){++e}}}export{a as l};
