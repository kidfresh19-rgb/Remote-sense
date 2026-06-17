How Auth Works in This App            
                                               
  This app uses JWT tokens (HS256). There is no
  username/password login form — authentication
  is entirely token-based. The token is minted
  manually for local dev.                      
  ──────                                      
  ### Step 1 — Generate a  RS_JWT_SECRET  (if  
  they don't have one)                        
                                               
  In their local  .env ,  RS_JWT_SECRET  must  
  be set. They can generate one with:          
                                               
    python -c "import secrets; print(secrets.token_urlsafe(36))"                          
                                               
  Then add it to their  .env :                
                                               
    RS_JWT_SECRET=<the_generated_value>        
                                               
  │ ⚠️ This is the root cause of the issue. If  
  │ the collaborator's  RS_JWT_SECRET  in their
  │ .env  is different from the one used to    
  sign                                        
  │ the token, they'll get  "malformed token"  
  /                                            
  │ 401 on every request. They must use the    
  │ exact same secret to both sign and verify.
  ──────                                      
  ### Step 2 — Mint a JWT Token                
                                               
  Run this Python snippet (from the project    
  root, with the venv active):                
                                               
    import base64, hashlib, hmac, json, time  
    from rs_core import get_settings          
                                               
    def b64(raw: bytes) -> str:                
        return base64.urlsafe_b64encode(raw).  
  rstrip(b"=").decode()                        
                                               
    header = b64(json.dumps({"alg": "HS256",  
  "typ": "JWT"}).encode())                    
    claims = b64(json.dumps({"sub": "you",    
  "roles": ["admin"], "exp": int(time.time()) +
  30 * 86400}).encode())                      
    sig = b64(hmac.new(get_settings().        
  jwt_secret.encode(), f"{header}.{claims}".  
  encode(), hashlib.sha256).digest())          
    print(f"{header}.{claims}.{sig}")          
                                               
  This mints a 30-day admin token for local    
  dev.                                        
  ──────                                      
  ### Step 3 — Put the Token in the Frontend  
                                               
  Set it in  frontend/.env :                  
                                               
    VITE_DEV_TOKEN=<the_token_from_step_2>    
  ──────                                      
  ### Available Roles & What They Can Access  
                                               
   Role        │ Permissions
  ─────────────┼───────────────────────────────
    viewer     │ Read analyses,
               │ interpretations, history
    analyst    │ + Annotate, run analysis
    publisher  │ + Push to gateway / publish
               │ interpretations
    admin      │ Everything
                                               
  The collaborator likely needs at least      
  analyst  or  admin  in their token's  roles  
  array to see all app functions.              
  ──────                                      
  ### 🔑 Summary of the Fix                    
                                               
  The collaborator must:                      
                                               
  1. Get the same  RS_JWT_SECRET  value that's
  in your  .env                                
  2. Run the mint script above to generate a  
  fresh token                                  
  3. Set  VITE_DEV_TOKEN  in their  frontend/.
  env                                          
  4. Restart the frontend dev server




---

 ## ✅ Created auth-setup.py                                                                                                           
                                                                                                                                         
  A self-contained (pure stdlib, zero dependencies) auth bootstrapper that automates all 3 steps from AUTH.md.                           
                                                                                                                                         
  ### Features                                                                                                                           
                                                                                                                                         
   Command                                                     │ What it does
  ─────────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────
    python auth-setup.py                                       │ Full setup: ensures JWT secret → mints 30-day admin token → writes 
                                                               │ frontend/.env 
    python auth-setup.py --verify                              │ Checks current setup: secret present? token valid? signature matches?
                                                               │ expired?
    python auth-setup.py --dry-run                             │ Preview all changes without writing anything
    python auth-setup.py --sub alice --roles analyst --days 7  │ Customize subject, roles, and token lifetime
    python auth-setup.py --force-new-secret                    │ Rotate the JWT secret (invalidates all existing tokens)
                                                                                                                                         
  ### What it does automatically                                                                                                         
                                                                                                                                         
  1. Reads/generates  RS_JWT_SECRET  in  .env  — skips generation if one already exists                                                  
  2. Mints an HS256 JWT with configurable  sub ,  roles , and expiry                                                                     
  3. Writes  VITE_DEV_TOKEN  into  frontend/.env                                                                                         
  4. Prints a clear summary with role permissions and next steps                                                                         
                                                                                                                                         
  ### Verify mode highlights                                                                                                             
                                                                                                                                         
  • Cross-checks the token signature against the secret (catches the "different secret" problem from AUTH.md)                            
  • Reports token expiry with days remaining                                                                                             
  • Shows decoded claims (subject, roles)