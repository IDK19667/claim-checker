/* Evident: the 3D desk (three.js r128 scene, camera, sheets, lamp and wood).
   Split from desk.js so the first screen does not download or parse it: it
   loads at the load event, beside three.js, only where the desk is drawn.
   desk.js calls window.__evidentDesk3d(O) once three.js and this file are in;
   O is desk.js's bridge, one accessor per name of desk.js this code uses, so
   reading or setting O.name reads or sets desk.js's own variable. */
window.__evidentDesk3d = function (O) {
  "use strict";
    var T = THREE;
    var canvas = document.getElementById("gl"), stage = document.getElementById("stage");
    var renderer;
    try {
      renderer = new T.WebGLRenderer({ canvas: canvas, antialias: true, powerPreference: "high-performance", preserveDrawingBuffer: O.params.has("shots") });
    } catch (e) { O.fallback(); return; }
    O.scene3d = true;
    // the page keeps its early layout (the text-mode card) until the first still frame: see handover()
    renderer.outputEncoding = T.sRGBEncoding;
    renderer.toneMapping = T.ACESFilmicToneMapping;
    var EXPOSURE = 0.95;
    renderer.toneMappingExposure = EXPOSURE;
    renderer.physicallyCorrectLights = true;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = T.PCFSoftShadowMap;
    var maxAniso = renderer.capabilities.getMaxAnisotropy();
    var DPR_CAP = Math.min(2, window.devicePixelRatio || 1), dpr = DPR_CAP;
    renderer.shadowMap.autoUpdate = false;

    var scene = new T.Scene();
    scene.background = new T.Color(0x0c0b0a);
    var camera = new T.PerspectiveCamera(36, 1, 0.05, 20);

    // ---- painted textures -----------------------------------------------------
    function cnv(w, h) { var c = document.createElement("canvas"); c.width = w; c.height = h; return c; }
    function hash(x, y) { var s = Math.sin(x * 127.1 + y * 311.7) * 43758.5453; return s - Math.floor(s); }
    var fibre = (function () {
      var c = cnv(256, 256), x = c.getContext("2d"), d = x.createImageData(256, 256);
      for (var i = 0; i < 256 * 256; i++) {
        var px = i % 256, py = (i / 256) | 0, v = hash(px, py);
        var streak = hash((px / 7) | 0, py) > 0.985 ? 0.5 : 0;
        var a = (v * 0.06 + streak * 0.06) * 255;
        d.data[i * 4] = 120; d.data[i * 4 + 1] = 105; d.data[i * 4 + 2] = 80; d.data[i * 4 + 3] = a;
      }
      x.putImageData(d, 0, 0); return c;
    })();
    function paperBase(x, w, h, tone, yellow) {
      x.fillStyle = tone; x.fillRect(0, 0, w, h);
      x.fillStyle = x.createPattern(fibre, "repeat"); x.fillRect(0, 0, w, h);
      var g = x.createRadialGradient(w * 0.5, h * 0.45, Math.min(w, h) * 0.35, w * 0.5, h * 0.5, Math.max(w, h) * 0.75);
      g.addColorStop(0, "rgba(160,125,70,0)"); g.addColorStop(1, "rgba(160,125,70," + yellow + ")");
      x.fillStyle = g; x.fillRect(0, 0, w, h);
    }
    function wrap(x, text, maxW) {
      var words = text.split(" "), lines = [], line = "";
      for (var i = 0; i < words.length; i++) {
        var test = line ? line + " " + words[i] : words[i];
        if (x.measureText(test).width > maxW && line) { lines.push(line); line = words[i]; } else line = test;
      }
      if (line) lines.push(line); return lines;
    }

    function tex(c, flip) {
      var t = new T.CanvasTexture(c); t.encoding = T.sRGBEncoding; t.anisotropy = maxAniso; t.flipY = flip !== false;
      t.minFilter = T.LinearMipmapLinearFilter; return t;
    }
    var SW = 1024, SH = 1448;
    function paintSheet(c, s, seed, scale) {
      var x = c.getContext("2d"), m = 92;
      x.setTransform(scale, 0, 0, scale, 0, 0);
      paperBase(x, SW, SH, "#f2eee4", 0.16 + seed * 0.08);
      x.fillStyle = "#615e5b"; x.font = '500 22px "IBM Plex Mono"'; x.textBaseline = "alphabetic";
      var j = s.journal.toUpperCase(); if (j.length > 44) j = j.slice(0, 43).trim() + ".";
      x.fillText(j, m, 128);
      x.textAlign = "right"; x.fillText(s.year, SW - m, 128); x.textAlign = "left";
      x.fillStyle = "#2f2e2b"; x.fillRect(m, 150, SW - 2 * m, 3);
      var size = 50, lines;
      do { x.font = '600 ' + size + 'px "Instrument Sans"'; lines = wrap(x, s.title, SW - 2 * m); size -= 2; } while (lines.length > 6 && size > 30);
      x.fillStyle = "#181715"; var y = 236, lh = size * 1.22;
      lines.forEach(function (l) { x.fillText(l, m, y); y += lh; });
      y += 14;
      x.font = '500 21px "IBM Plex Mono"'; x.fillStyle = "#615e5b";
      x.fillText((s.design || "Study").toUpperCase() + "   PMID " + s.pmid, m, y); y += 40;
      x.fillStyle = "#cfcabe"; x.fillRect(m, y, SW - 2 * m, 2); y += 52;
      x.font = '500 20px "IBM Plex Mono"'; x.fillStyle = "#6f6c69"; x.fillText("ABSTRACT", m, y); y += 36;
      var col = (SW - 2 * m - 44) / 2, cx = [m, m + col + 44], ci = 0, top = y, r = seed * 997;
      while (ci < 2) {
        var para = 4 + Math.floor(hash(r, y) * 6);
        for (var k = 0; k < para && y < SH - 130; k++) {
          var wl = (k === para - 1) ? 0.3 + hash(r + k, y) * 0.5 : 0.9 + hash(y, r + k) * 0.1;
          x.fillStyle = "rgba(40,39,37," + (0.22 + hash(k, y) * 0.08) + ")";
          var px = cx[ci], rest = col * wl;
          while (rest > 8) { var w = Math.min(rest, 22 + hash(px, y + r) * 70); x.fillRect(px, y - 9, w - 7, 10); px += w; rest -= w; }
          y += 24;
        }
        y += 18;
        if (y >= SH - 130) { ci++; y = top; }
      }
      if (s.n) { x.font = '500 18px "IBM Plex Mono"'; x.fillStyle = "#7f7c79"; x.textAlign = "center"; x.fillText(String(s.n), SW / 2, SH - 62); x.textAlign = "left"; }
    }
    var CW = 1600, CH = 960, VERDICT_W = 1420;
    function cardWords() { return { claim: O.txt(document, "#claim-echo"), verdict: O.txt(document, "#stamp"), tldr: O.txt(document, "#tldr") }; }
    var CLAIM_FITS = [{ size: 54, lines: 1 }, { size: 46, lines: 2 }, { size: 40, lines: 2 }, { size: 34, lines: 3 }];
    // count(size) is how many lines the claim takes at that size; the first fit that holds it, else the last, clamped
    function fitClaim(count) { for (var i = 0; i < CLAIM_FITS.length; i++) { var f = CLAIM_FITS[i], n = count(f.size); if (n <= f.lines) return { size: f.size, lines: n, clamp: false }; } return { size: 34, lines: 3, clamp: true }; }
    function claimBase(f) { return Math.min(252, 300 - (f.lines - 1) * f.size * 1.2); }
    var TLDR_FITS = [54, 46, 40];
    function fitTldr(count) {
      for (var i = 0; i < TLDR_FITS.length; i++) { var sz = TLDR_FITS[i], lh = Math.round(sz * 1.3), max = Math.floor((CH - 40 - 618) / lh) + 1, n = count(sz); if (n <= max) return { size: sz, lh: lh, lines: n }; }
      return { size: 40, lh: 52, lines: Math.floor((CH - 40 - 618) / 52) + 1 };
    }
    // at most n lines; a cut last line ends in an ellipsis
    function clampLines(x, lines, n, maxW) {
      if (lines.length <= n) return lines;
      var out = lines.slice(0, n), last = out[n - 1];
      while (last.indexOf(" ") > 0 && x.measureText(last + "\u2026").width > maxW) last = last.slice(0, last.lastIndexOf(" "));
      out[n - 1] = last.replace(/[\s,.;:]+$/, "") + "\u2026"; return out;
    }
    function paintCard(c, withVerdict, scale) {
      var x = c.getContext("2d");
      x.setTransform(scale || 1, 0, 0, scale || 1, 0, 0);
      paperBase(x, CW, CH, "#efe6d1", 0.14);
      x.fillStyle = "rgba(64,62,59,0.42)"; x.fillRect(0, 150, CW, 3);
      x.fillStyle = "rgba(90,84,74,0.16)";
      for (var y = 150 + 66; y < CH - 20; y += 66) x.fillRect(0, y, CW, 2);
      if (!withVerdict) return;
      var m = 96, words = cardWords();
      x.fillStyle = "#514f4c"; x.font = '500 32px "IBM Plex Mono"'; x.fillText("THE CLAIM YOU CHECKED", m, 108);
      // the claim: one line at 54, two at 46 or 40, three at 34 and then an ellipsis (layoutCard sets the HTML card the same way)
      var cf = fitClaim(function (size) { x.font = '400 ' + size + 'px "Instrument Sans"'; return wrap(x, "“" + words.claim + "”", CW - 2 * m).length; });
      x.fillStyle = "#2a2926"; x.font = '400 ' + cf.size + 'px "Instrument Sans"';
      var cl = clampLines(x, wrap(x, "“" + words.claim + "”", CW - 2 * m), cf.lines, CW - 2 * m), cy = claimBase(cf);
      cl.forEach(function (l) { x.fillText(l, m, cy); cy += cf.size * 1.2; });
      // the verdict: 200 units, smaller when the words are wider than the card
      x.fillStyle = "#12100f"; x.font = '700 200px "Bricolage Grotesque"';
      var vw = x.measureText(words.verdict).width, vs = vw > VERDICT_W ? Math.floor(200 * VERDICT_W / vw) : 200;
      x.font = '700 ' + vs + 'px "Bricolage Grotesque"'; x.fillText(words.verdict, m - 10, 490);
      var tf = fitTldr(function (size) { x.font = '400 ' + size + 'px "Instrument Sans"'; return wrap(x, words.tldr, CW - 2 * m).length; });
      x.fillStyle = "#262522"; x.font = '400 ' + tf.size + 'px "Instrument Sans"';
      var ls = clampLines(x, wrap(x, words.tldr, CW - 2 * m), tf.lines, CW - 2 * m), yy = 618;
      ls.forEach(function (l) { x.fillText(l, m, yy); yy += tf.lh; });
    }
    function softRect(w, h, blur, alpha) {
      var c = cnv(w, h), x = c.getContext("2d");
      x.filter = "blur(" + blur + "px)"; x.fillStyle = "rgba(0,0,0," + alpha + ")";
      x.fillRect(blur * 2.2, blur * 2.2, w - blur * 4.4, h - blur * 4.4);
      return new T.CanvasTexture(c);
    }
    function blob(alpha) {
      var c = cnv(256, 256), x = c.getContext("2d"), g = x.createRadialGradient(128, 128, 0, 128, 128, 128);
      g.addColorStop(0, "rgba(0,0,0," + alpha + ")"); g.addColorStop(0.5, "rgba(0,0,0," + alpha * 0.5 + ")"); g.addColorStop(1, "rgba(0,0,0,0)");
      x.fillStyle = g; x.fillRect(0, 0, 256, 256); return new T.CanvasTexture(c);
    }

    // ---- the room ---------------------------------------------------------------
    var DESK_W = 1.9, DESK_D = 0.92, DESK_T = 0.04, WOOD_TILE = 1.0, woodState = "loading";
    var woodMat = new T.MeshPhysicalMaterial({ color: 0x5b4636, roughness: 0.5, clearcoat: 0.35, clearcoatRoughness: 0.32 });
    (function () {
      var fb = cnv(512, 512), x = fb.getContext("2d"); x.fillStyle = "#4a3426"; x.fillRect(0, 0, 512, 512);
      for (var i = 0; i < 260; i++) { x.fillStyle = "rgba(" + (hash(i, 1) > 0.5 ? "20,12,8" : "110,80,58") + "," + (0.05 + hash(i, 2) * 0.12) + ")"; x.fillRect(0, hash(i, 3) * 512, 512, 1 + hash(i, 4) * 3); }
      var fallbackTex = tex(fb); fallbackTex.wrapS = fallbackTex.wrapT = T.RepeatWrapping; fallbackTex.repeat.set(1, 1);
      woodMat.map = fallbackTex; woodMat.color.set(0xb09a8a); woodMat.needsUpdate = true;
      // the photo (1024 wide by 2048 tall, WebP, 190 KB: the grain is in the height, so only the width was halved), grain running along the image's width; the desk's UVs are in metres (see deskGeo), so one tile is WOOD_TILE metres square on every face
      // the photo has been loading since the page began (woodImg), alongside the library
      function woodOk() {
        var t = new T.Texture(O.woodImg); t.needsUpdate = true;
        t.encoding = T.sRGBEncoding; t.anisotropy = maxAniso; t.wrapS = t.wrapT = T.RepeatWrapping;
        t.minFilter = T.LinearMipmapLinearFilter; t.repeat.set(1 / WOOD_TILE, 1 / WOOD_TILE);
        woodMat.map = t; woodMat.color.set(0x8a7466); woodState = "ok"; O.wake();
      }
      function woodBad() { woodState = "failed"; woodMat.map = null; woodMat.color.set(0x5b4130); woodMat.needsUpdate = true; O.wake(); }
      if (O.woodImg.complete) { if (O.woodImg.naturalWidth) woodOk(); else woodBad(); }
      else { O.woodImg.addEventListener("load", woodOk); O.woodImg.addEventListener("error", woodBad); }
    })();
    // fine grain, closer than the photo can hold: a tileable 256 px pore and fibre map, sampled at two unrelated repeats
    // and multiplied in at mean 1, so it adds texture without changing the wood's colour; the mipmaps average it away at a distance
    var detailTex = (function () {
      var N = 256, c = cnv(N, N), x = c.getContext("2d"), d = x.createImageData(N, N), f = new Float32Array(N * N);
      function layer(cx, cy, amp, seed) {
        // value noise on a wrapped cx by cy lattice, so the tile joins seamlessly
        for (var py = 0; py < N; py++) for (var px = 0; px < N; px++) {
          var gx = px / N * cx, gy = py / N * cy, ix = Math.floor(gx), iy = Math.floor(gy), fx = gx - ix, fy = gy - iy;
          var sx = fx * fx * (3 - 2 * fx), sy = fy * fy * (3 - 2 * fy);
          var a = hash(ix % cx + seed, iy % cy), b = hash((ix + 1) % cx + seed, iy % cy), cc = hash(ix % cx + seed, (iy + 1) % cy), dd = hash((ix + 1) % cx + seed, (iy + 1) % cy);
          f[py * N + px] += amp * ((a + (b - a) * sx) * (1 - sy) + (cc + (dd - cc) * sx) * sy - 0.5);
        }
      }
      layer(2, 32, 0.5, 11); layer(4, 64, 0.35, 23); layer(32, 128, 0.18, 37);
      var mean = 0; for (var i = 0; i < N * N; i++) mean += f[i]; mean /= N * N;
      for (i = 0; i < N * N; i++) { var v = 128 + (f[i] - mean) * 150; v = v < 0 ? 0 : v > 255 ? 255 : v; d.data[i * 4] = d.data[i * 4 + 1] = d.data[i * 4 + 2] = v; d.data[i * 4 + 3] = 255; }
      x.putImageData(d, 0, 0);
      var t = new T.CanvasTexture(c); t.wrapS = t.wrapT = T.RepeatWrapping; t.anisotropy = maxAniso; t.minFilter = T.LinearMipmapLinearFilter; return t;
    })();
    woodMat.onBeforeCompile = function (sh) {
      sh.uniforms.detailMap = { value: detailTex };
      sh.fragmentShader = "uniform sampler2D detailMap;\n" + sh.fragmentShader.replace("#include <map_fragment>",
        "#include <map_fragment>\n#ifdef USE_MAP\n  float dA = texture2D(detailMap, vUv * vec2(47.4, 47.4)).r, dB = texture2D(detailMap, vUv * vec2(122.6, 117.8) + vec2(0.37, 0.61)).r;\n  diffuseColor.rgb *= (0.75 + 0.5 * dA) * (0.8 + 0.4 * dB);\n#endif");
    };
    // UVs in metres, so the grain keeps its true proportions on the top and on the edges, and always runs along the desk's long side
    function deskGeo() {
      var g = new T.BoxGeometry(DESK_W, DESK_T, DESK_D), pos = g.attributes.position, nor = g.attributes.normal, uv = g.attributes.uv;
      for (var i = 0; i < pos.count; i++) {
        var x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i), nx = Math.abs(nor.getX(i)), ny = Math.abs(nor.getY(i));
        if (ny > 0.5) uv.setXY(i, x, z); else if (nx > 0.5) uv.setXY(i, z, y); else uv.setXY(i, x, y);
      }
      uv.needsUpdate = true; return g;
    }
    var desk = new T.Mesh(deskGeo(), woodMat);
    desk.position.y = -DESK_T / 2; desk.receiveShadow = true; scene.add(desk);
    var darkWood = new T.MeshStandardMaterial({ color: 0x1a130f, roughness: 0.7 });
    [[-1, -1], [1, -1], [-1, 1], [1, 1]].forEach(function (p) {
      var leg = new T.Mesh(new T.BoxGeometry(0.05, 0.72, 0.05), darkWood);
      leg.position.set(p[0] * (DESK_W / 2 - 0.06), -0.4, p[1] * (DESK_D / 2 - 0.06)); scene.add(leg);
    });
    var apron = new T.Mesh(new T.BoxGeometry(DESK_W - 0.1, 0.08, DESK_D - 0.1), darkWood); apron.position.y = -0.08; scene.add(apron);
    var wall = new T.Mesh(new T.PlaneGeometry(8, 4), new T.MeshStandardMaterial({ color: 0x413f3d, roughness: 0.96 }));
    wall.position.set(0, 0.9, -0.52); wall.receiveShadow = true; scene.add(wall);
    var floor = new T.Mesh(new T.PlaneGeometry(10, 10), new T.MeshStandardMaterial({ color: 0x141210, roughness: 0.9 }));
    floor.rotation.x = -Math.PI / 2; floor.position.y = -0.76; scene.add(floor);

    // ---- the lamp ---------------------------------------------------------------
    var metal = new T.MeshStandardMaterial({ color: 0x141515, roughness: 0.38, metalness: 0.55 });
    var chrome = new T.MeshStandardMaterial({ color: 0x8e918f, roughness: 0.28, metalness: 0.9 });
    var BASE = new T.Vector3(-0.64, 0, -0.3), ELBOW = new T.Vector3(-0.66, 0.44, -0.36), HEAD = new T.Vector3(-0.27, 0.6, 0.0);
    var lampBase = new T.Mesh(new T.CylinderGeometry(0.085, 0.095, 0.022, 64), metal);
    lampBase.position.copy(BASE).add(new T.Vector3(0, 0.011, 0)); lampBase.castShadow = true; lampBase.receiveShadow = true; scene.add(lampBase);
    function rod(a, b, r, mat) {
      var d = new T.Vector3().subVectors(b, a), m = new T.Mesh(new T.CylinderGeometry(r, r, d.length(), 16), mat);
      m.position.copy(a).add(b).multiplyScalar(0.5); m.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), d.normalize());
      m.castShadow = true; scene.add(m); return m;
    }
    rod(new T.Vector3(BASE.x, 0.02, BASE.z), ELBOW, 0.0065, chrome);
    rod(ELBOW, HEAD, 0.0065, chrome);
    [ELBOW, new T.Vector3(BASE.x, 0.03, BASE.z)].forEach(function (p) { var j = new T.Mesh(new T.SphereGeometry(0.013, 20, 12), metal); j.position.copy(p); j.castShadow = true; scene.add(j); });
    var head = new T.Group(); head.position.copy(HEAD); scene.add(head);
    var shadeR = 0.1;
    var shadeOut = new T.Mesh(new T.SphereGeometry(shadeR, 64, 24, 0, Math.PI * 2, 0, Math.PI * 0.5), metal);
    shadeOut.scale.y = 0.9; shadeOut.castShadow = true; head.add(shadeOut);
    // inside the shade: a warm reflector, brightest near the bulb, falling off to the rim
    var reflMap = (function () {
      var c = cnv(64, 256), x = c.getContext("2d"), g = x.createLinearGradient(0, 0, 0, 256);
      g.addColorStop(0, "#ffffff"); g.addColorStop(0.45, "#c9b79a"); g.addColorStop(1, "#4a4030");
      x.fillStyle = g; x.fillRect(0, 0, 64, 256); var t = new T.CanvasTexture(c); t.encoding = T.sRGBEncoding; return t;
    })();
    var shadeInMat = new T.MeshStandardMaterial({ color: 0xd9d2c4, roughness: 0.55, side: T.BackSide, emissive: 0xffd2a0, emissiveMap: reflMap, emissiveIntensity: 1.1 });
    var shadeIn = new T.Mesh(new T.SphereGeometry(shadeR * 0.985, 64, 24, 0, Math.PI * 2, 0, Math.PI * 0.5), shadeInMat);
    shadeIn.scale.y = 0.9; head.add(shadeIn);
    var rim = new T.Mesh(new T.TorusGeometry(shadeR, 0.0035, 8, 96), metal); rim.rotation.x = Math.PI / 2; head.add(rim);
    var knuckle = new T.Mesh(new T.SphereGeometry(0.016, 20, 12), metal); knuckle.position.y = shadeR * 0.9; head.add(knuckle);
    var socket = new T.Mesh(new T.CylinderGeometry(0.014, 0.016, 0.03, 24), new T.MeshStandardMaterial({ color: 0x2a2826, roughness: 0.5, metalness: 0.4 }));
    socket.position.y = 0.068; head.add(socket);
    var bulbMat = new T.MeshBasicMaterial({ color: 0xfff3df });
    var bulb = new T.Mesh(new T.SphereGeometry(0.027, 32, 20), bulbMat); bulb.scale.y = 1.15; bulb.position.y = 0.032; head.add(bulb);

    var LEAK_I = 0.3;
    var spot = new T.SpotLight(0xffcf98, 2.7, 0, 0.74, 1, 2);
    scene.add(spot); scene.add(spot.target);
    spot.castShadow = true; spot.shadow.mapSize.set(2048, 2048); spot.shadow.bias = -0.00025; spot.shadow.normalBias = 0.012;
    spot.shadow.camera.near = 0.05; spot.shadow.camera.far = 2.6; spot.shadow.focus = 1;
    var leak = new T.PointLight(0xffc68c, LEAK_I, 0, 2); leak.position.copy(HEAD).add(new T.Vector3(0.03, 0.16, -0.1)); scene.add(leak);
    var hemi = new T.HemisphereLight(0xb2aeaa, 0x1a120c, 0.12); scene.add(hemi);
    var cool = new T.DirectionalLight(0xa6a29e, 0.06); cool.position.set(2, 1.2, 1.5); scene.add(cool);
    var LAMP_I = spot.intensity, LAMP_ANGLE = spot.angle;
    var key = new T.SpotLight(0xfff1de, 0, 0, 0.32, 1, 2);
    key.position.set(0.12, 0.42, 0.62); key.target.position.set(-0.1, 0.047, 0.13); scene.add(key); scene.add(key.target);
    var aimNow = new T.Vector3(), aimDir = new T.Vector3(), DOWN = new T.Vector3(0, -1, 0);
    function aimLamp(p) {
      aimNow.copy(p); aimDir.subVectors(p, HEAD).normalize();
      head.quaternion.setFromUnitVectors(DOWN, aimDir);
      spot.position.copy(HEAD).addScaledVector(aimDir, 0.04);
      spot.target.position.copy(p);
    }

    // ---- contact shadows ------------------------------------------------------------
    var sheetShadowTex = softRect(256, 340, 14, 0.75), cardShadowTex = softRect(320, 200, 22, 0.8), blobTex = blob(0.85);
    function shadowPlane(tx, w, h, opacity) {
      var m = new T.Mesh(new T.PlaneGeometry(w, h), new T.MeshBasicMaterial({ map: tx, transparent: true, depthWrite: false, opacity: opacity, color: 0x000000 }));
      m.rotation.x = -Math.PI / 2; m.renderOrder = 1; scene.add(m); return m;
    }
    var baseAO = shadowPlane(blobTex, 0.3, 0.3, 0.9); baseAO.position.set(BASE.x, 0.0004, BASE.z);

    // ---- two closed books in the dim, for depth --------------------------------
    (function () {
      var cloth = new T.MeshStandardMaterial({ color: 0x2a2d29, roughness: 0.85 });
      var cloth2 = new T.MeshStandardMaterial({ color: 0x3a2f26, roughness: 0.8 });
      var pages = new T.MeshStandardMaterial({ color: 0xcfc6b2, roughness: 0.95 });
      function book(w, t, d, mat, x, y, z, yaw) {
        var g = new T.Group();
        var cover = new T.Mesh(new T.BoxGeometry(w, t, d), mat); cover.castShadow = cover.receiveShadow = true; g.add(cover);
        var block = new T.Mesh(new T.BoxGeometry(w - 0.006, t * 0.82, d - 0.012), pages); block.position.x = 0.004; g.add(block);
        g.position.set(x, y + t / 2, z); g.rotation.y = yaw; scene.add(g);
      }
      book(0.17, 0.032, 0.24, cloth, 0.74, 0, -0.32, 0.12);
      book(0.15, 0.024, 0.21, cloth2, 0.75, 0.032, -0.325, -0.08);
      var ao = new T.Mesh(new T.PlaneGeometry(0.26, 0.32), new T.MeshBasicMaterial({ map: blob(0.8), transparent: true, depthWrite: false, color: 0x000000, opacity: 0.7 }));
      ao.rotation.x = -Math.PI / 2; ao.rotation.z = 0.12; ao.position.set(0.74, 0.0003, -0.32); scene.add(ao);
    })();

    // ---- the pencil --------------------------------------------------------------
    var pencil = new T.Group();
    var pLen = 0.165, pR = 0.0037;
    var paint = new T.MeshStandardMaterial({ color: 0x343331, roughness: 0.42, metalness: 0.05 });
    var body = new T.Mesh(new T.CylinderGeometry(pR, pR, pLen, 6), paint); body.castShadow = true; body.receiveShadow = true; pencil.add(body);
    var cedar = new T.Mesh(new T.CylinderGeometry(0.0011, pR * 0.93, 0.02, 6), new T.MeshStandardMaterial({ color: 0xc9a27a, roughness: 0.8 }));
    cedar.position.y = pLen / 2 + 0.01; cedar.castShadow = true; pencil.add(cedar);
    var lead = new T.Mesh(new T.ConeGeometry(0.0011, 0.004, 8), new T.MeshStandardMaterial({ color: 0x1b1b1b, roughness: 0.35, metalness: 0.4 }));
    lead.position.y = pLen / 2 + 0.022; pencil.add(lead);
    var ferrule = new T.Mesh(new T.CylinderGeometry(pR * 1.04, pR * 1.04, 0.012, 16), chrome); ferrule.position.y = -pLen / 2 - 0.006; pencil.add(ferrule);
    var eraser = new T.Mesh(new T.CylinderGeometry(pR * 0.98, pR * 0.98, 0.008, 16), new T.MeshStandardMaterial({ color: 0x3b3532, roughness: 0.9 })); eraser.position.y = -pLen / 2 - 0.016; pencil.add(eraser);
    pencil.rotation.set(0, 0.42, Math.PI / 2); body.rotation.y = Math.PI / 6;
    pencil.position.set(0.38, pR * 0.9, 0.34); scene.add(pencil);
    var pencilAO = shadowPlane(blobTex, 0.2, 0.03, 0.55); pencilAO.position.set(0.38, 0.0003, 0.345); pencilAO.rotation.z = 0.42;

    // ---- sheets ------------------------------------------------------------------
    var PW = 0.21, PH = 0.297, PT = 0.0021;
    // where the sheets fan out under the lamp: relied on in two rows in the brightest part, read at the light's edge, off topic face down beyond
    var FAN = { ax: -0.54, dx: 0.098, az: -0.02, bx: 0.05, bz: 0.17, rx: 0.2, rz: -0.12, ox: 0.5, oz: 0.12 };
    var edgeMat = new T.MeshStandardMaterial({ color: 0xe8e2d5, roughness: 0.95, emissive: 0x2a2620, emissiveIntensity: 1 });
    var backMat = (function () { var c = cnv(256, 362), x = c.getContext("2d"); paperBase(x, 256, 362, "#ece7dc", 0.2); return new T.MeshStandardMaterial({ map: tex(c), roughness: 0.95 }); })();
    function sheetGeo(seed, i) {
      var g = new T.BoxGeometry(PW, PT, PH, 14, 1, 20), pos = g.attributes.position;
      var cA = 0.0018 + seed * 0.0022, corner = (i % 2 ? 1 : -1);
      for (var v = 0; v < pos.count; v++) {
        var px = pos.getX(v) / (PW / 2), pz = pos.getZ(v) / (PH / 2);
        pos.setY(v, pos.getY(v) + 0.0005 * px * px + cA * Math.pow(Math.max(0, px * corner * 0.5 + pz * 0.5), 4));
      }
      g.computeVertexNormals();
      // the four edges share one material: drawn as one group, so a sheet is three draw calls, not six (and three in
      // the shadow pass). The same triangles with the same materials
      var idx = g.index.array, gs = g.groups, order = [0, 1, 4, 5, 2, 3], out = new (idx.constructor)(idx.length), at = 0, ng = [];
      order.forEach(function (gi) { var q = gs[gi]; out.set(idx.subarray(q.start, q.start + q.count), at);
        var mi = gi === 2 ? 2 : gi === 3 ? 3 : 0, last = ng[ng.length - 1];
        if (last && last.materialIndex === mi) last.count += q.count; else ng.push({ start: at, count: q.count, materialIndex: mi });
        at += q.count; });
      g.setIndex(new T.BufferAttribute(out, 1)); g.clearGroups(); ng.forEach(function (q) { g.addGroup(q.start, q.count, q.materialIndex); });
      return g;
    }
    var sheets = [], STACK = new T.Vector3(-0.46, 0, 0.0), GAP = 0.0004, sheetGen = 0;
    // one blank paper face for every sheet until its own face is painted, after the first frame
    var blankSheet = (function () { var c = cnv(128, 181), x = c.getContext("2d"); paperBase(x, 128, 181, "#f2eee4", 0.2); return tex(c); })();
    // relied on: rows of up to six in the brightest part, centred under the lamp, split evenly over the rows;
    // read (and background only, after them): rows of four at the light's edge; off topic: face down beyond
    var FAN_CX = FAN.ax + 2.5 * FAN.dx, SIDE_YAW = [0.2, 0.3, 0.14, 0.26];
    function dropSheets() {
      if (hiSlot) dropHi();
      sheets.forEach(function (o) {
        scene.remove(o.mesh); scene.remove(o.sh); o.mesh.geometry.dispose();
        var f = o.mesh.userData.face; if (f.map && f.map !== blankSheet) f.map.dispose(); f.dispose();
        o.sh.geometry.dispose(); o.sh.material.dispose();
      });
      sheets = []; casters = null; sheetGen++;
    }
    function buildSheets() {
      dropSheets();
      var relied = O.inGroup("relied"), side = O.inGroup("read").concat(O.inGroup("back")), offT = O.inGroup("off");
      var R = relied.length, rows = Math.max(1, Math.ceil(R / 6)), per = Math.ceil(R / rows);
      O.CHECK.studies.forEach(function (s, i) {
        var seed = hash(i * 3.1, 7.7);
        var fmap = blankSheet; var face = new T.MeshStandardMaterial({ map: fmap, roughness: 0.88, metalness: 0, emissive: 0xffffff, emissiveMap: fmap, emissiveIntensity: 0 });
        var mesh = new T.Mesh(sheetGeo(seed, i), [edgeMat, edgeMat, face, backMat, edgeMat, edgeMat]);
        mesh.castShadow = true; mesh.receiveShadow = true; mesh.userData.n = s.n; mesh.userData.face = face;
        scene.add(mesh);
        var sh = shadowPlane(sheetShadowTex, PW * 1.32, PH * 1.26, 0.0);
        var rest = new T.Vector3(STACK.x + (hash(i, 1) - 0.5) * 0.016, PT / 2 + i * (PT + GAP), STACK.z + (hash(i, 2) - 0.5) * 0.014);
        var restYaw = (hash(i, 3) - 0.5) * 0.09;
        var from = new T.Vector3(rest.x + 0.12 + hash(i, 4) * 0.08, rest.y + 0.34, rest.z + 0.22 + hash(i, 5) * 0.06);
        var fan, fanYaw, k, roll0 = 0, order;
        if (s.group === "relied") {
          k = relied.indexOf(s); order = k;
          var row = Math.floor(k / per), kk = k - row * per, inRow = Math.min(per, R - row * per), c = kk - (inRow - 1) / 2;
          fan = new T.Vector3(FAN_CX + c * FAN.dx + row * FAN.bx, PT / 2 + k * (PT + 0.0008), FAN.az + row * FAN.bz + (hash(k, 9) - 0.5) * 0.02);
          fanYaw = c * 0.035 + (hash(k, 8) - 0.5) * 0.05;
        } else if (s.group === "off") {
          k = offT.indexOf(s); order = R + side.length + k;
          fan = new T.Vector3(FAN.ox + k * 0.008, PT / 2 + 0.0002 + k * (PT + 0.0004), FAN.oz - k * 0.006); fanYaw = -0.3 + k * 0.05; roll0 = Math.PI;
        } else {
          k = side.indexOf(s); order = R + k;
          var sr = Math.floor(k / 4), sk = k % 4;
          fan = new T.Vector3(FAN.rx + sk * 0.07 + sr * 0.03, PT / 2 + k * (PT + 0.0008), FAN.rz + sk * 0.045 + sr * 0.13);
          fanYaw = SIDE_YAW[sk];
        }
        sheets.push({ s: s, mesh: mesh, sh: sh, rest: rest, restYaw: restYaw, from: from, fromYaw: restYaw + (hash(i, 6) - 0.5) * 0.9, fan: fan, fanYaw: fanYaw, fanRoll: roll0, order: order, lift: 0, hover: 0, seed: seed, painted: false });
      });
    }

    // ---- the index card ----------------------------------------------------------
    var CARD_W = 0.127, CARD_H = 0.0762, CARD_T = 0.0009;
    var card = new T.Group();
    var cBlank = cnv(CW / 4, CH / 4); paintCard(cBlank, false, 0.25);
    var cardSide = new T.MeshStandardMaterial({ color: 0xe6dcc6, roughness: 0.9 });
    var cbMap = tex(cBlank), cvMap = cbMap, csMap = cbMap, cardPainted = false;
    // the large faces are painted only when the check runs: the standing face, then the printed verdict
    // later: a result landing on a desk already drawing paints the large plain standing face in its own task after the
    // answer is on screen (it is only seen once the card has settled, under the HTML words; until then the card stands
    // with its printed face)
    var standGen = 0;
    function paintCardFaces(later) {
      if (cardPainted) return; cardPainted = true;
      paintVerdictFace();
      var gen = ++standGen;
      // the plain face carries no words of the result: painted once, kept for every answer after
      function stand() {
        if (gen !== standGen || !cardPainted || csMap !== cbMap) return;
        var cStand = cnv(CW * 1.6, CH * 1.6); paintCard(cStand, false, 1.6); var old = csMap; csMap = tex(cStand);
        if (vMat.map === old) { vMat.map = csMap; vMat.emissiveMap = csMap; }
        O.wake();
      }
      if (later) setTimeout(stand, 0); else stand();
      if (!later) { vMat.map = csMap; vMat.emissiveMap = csMap; }
      vMat.needsUpdate = true;
    }
    // the printed faces use the page's web fonts; one painted before they arrived is painted again when they do
    var cvFontOk = false;
    function fontsOk() {
      if (!document.fonts || !document.fonts.check) return false;
      return ['700 40px "Bricolage Grotesque"', '600 40px "Bricolage Grotesque"', '400 40px "Instrument Sans"', '600 40px "Instrument Sans"', '500 20px "IBM Plex Mono"']
        .every(function (f) { return document.fonts.check(f); });
    }
    function paintVerdictFace() {
      var c = cnv(CW, CH); paintCard(c, true); var old = cvMap; cvMap = tex(c); cvFontOk = fontsOk();
      if (vMat.map === old) { vMat.map = vMat.emissiveMap = cvMap; vMat.needsUpdate = true; }
      if (old && old !== cbMap) old.dispose();
    }
    function fontsArrived() {
      if (!fontsOk()) return;
      if (cardPainted && !cvFontOk) paintVerdictFace();
      if (csvMap && !csvFontOk) { csvKey = ""; prepareStandFace(); }
      cardKey = ""; O.wake();
    }
    if (document.fonts) {
      if (document.fonts.addEventListener) document.fonts.addEventListener("loadingdone", function () { fontsArrived(); });
      if (document.fonts.ready) document.fonts.ready.then(fontsArrived);
    }
    var vMat = new T.MeshStandardMaterial({ map: csMap, roughness: 0.82, emissive: 0xffffff, emissiveMap: csMap, emissiveIntensity: 0 });
    var bMat = new T.MeshStandardMaterial({ map: cbMap, roughness: 0.85 });
    var cardBox = new T.Mesh(new T.BoxGeometry(CARD_W, CARD_H, CARD_T), [cardSide, cardSide, cardSide, cardSide, vMat, bMat]);
    cardBox.castShadow = true; cardBox.receiveShadow = true; card.add(cardBox);
    scene.add(card);
    var CARD_REST = new T.Vector3(0.2, CARD_T / 2, 0.3), CARD_YAW = -0.16;
    var CARD_LEAN = 0.12, CARD_STAND = new T.Vector3(-0.1, CARD_H / 2 * Math.cos(CARD_LEAN) + 0.004, 0.13), CARD_DOWN = new T.Vector3(0.13, CARD_T / 2, 0.27);
    var Q_STAND = new T.Quaternion().setFromEuler(new T.Euler(-CARD_LEAN, 0, 0));
    var qDown = new T.Quaternion().setFromEuler(new T.Euler(Math.PI / 2, 0, 0)); // blank face up
    var qUp = new T.Quaternion().setFromEuler(new T.Euler(-Math.PI / 2, 0, 0));  // verdict face up
    var qYaw = new T.Quaternion().setFromAxisAngle(new T.Vector3(0, 1, 0), CARD_YAW);
    var Q_REST_BLANK = qYaw.clone().multiply(qDown), Q_REST_VERDICT = qYaw.clone().multiply(qUp);
    var cardSh = shadowPlane(cardShadowTex, CARD_W * 1.6, CARD_H * 1.8, 0.8);

    // ---- camera stops (desktop and phone framings) and where the lamp points ------
    function V(x, y, z) { return new T.Vector3(x, y, z); }
    var KEYS = {
      wide: [
        { p: V(0.74, 0.52, 1.32), t: V(-0.24, 0.2, -0.1), fov: 36, fx: 0.36, fy: 0.52 },
        { p: V(-0.1, 0.1, 0.52), t: V(-0.1, 0.047, 0.13), fov: 26, fx: 0.33, fy: 0.5 },
        { p: V(-0.2, 0.46, 0.8), t: V(-0.44, 0.0, -0.12), fov: 34, fx: 0.32, fy: 0.55 },
        { p: V(0.14, 1.53, 1.09), t: V(-0.07, 0.0, -0.05), fov: 50, fx: 0.71, fy: 0.5 },
        { p: V(0.75, 0.78, 1.5), t: V(-0.2, 0.06, -0.08), fov: 36, fx: 0.74, fy: 0.5 },
        { p: V(1.15, 1.4, 2.05), t: V(-0.12, 0.04, -0.08), fov: 34, fx: 0.62, fy: 0.5 }
      ],
      tall: [
        { p: V(0.25, 1.3, 1.6), t: V(-0.18, 0.0, 0.04), fov: 56, fx: 0.5, fy: 0.86 },
        { p: V(-0.1, 0.105, 0.6), t: V(-0.1, 0.047, 0.13), fov: 34, fx: 0.5, fy: 0.34 },
        { p: V(-0.36, 0.86, 0.78), t: V(-0.44, 0.0, -0.12), fov: 48, fx: 0.5, fy: 0.7 },
        { p: V(-0.06, 2.0, 0.92), t: V(-0.12, 0.0, -0.05), fov: 54, fx: 0.5, fy: 0.24 },
        { p: V(0.5, 1.2, 1.9), t: V(-0.15, 0.0, -0.05), fov: 46, fx: 0.5, fy: 0.5 },
        { p: V(0.7, 1.55, 2.0), t: V(-0.12, 0.0, -0.05), fov: 44, fx: 0.5, fy: 0.7 }
      ]
    };
    var AIMS = [V(-0.22, 0, 0), V(-0.1, 0.03, 0.13), V(-0.44, 0, 0.06), V(-0.22, 0, -0.02), V(-0.2, 0, 0), V(-0.2, 0, 0)];
    var EXPO = [1, 1, 1, 0.92, 0.42, 0.72];
    // the framings of the stops the page prints (before a check, all six: the camera rests on the first)
    var K, AK, EK, CL = 5, posCurve, tgtCurve, AIMS6 = AIMS, EXPO6 = EXPO;
    // ---- the phone's first screen (L2): before a check, on a phone held upright, the first stop is the desktop's own
    // view of the lamp, fitted by fitPhoneHome() into the band between the strip and the words (desk.css --lamp-band)
    var PHONE_HOME = { p: KEYS.wide[0].p.clone(), t: KEYS.wide[0].t.clone(), fov: 36, fx: 0.5, fy: 0.3 };
    // what must show in the band: the base, the stem and elbow, the shade, and the pool of lit wood under it
    // (the shade as its crown, knuckle and rim all round, so its top keeps clear air under the strip)
    var LAMP_PTS = [V(-0.74, 0, -0.38), V(-0.54, 0, -0.2), V(-0.66, 0.46, -0.36), V(-0.27, 0.71, 0), V(-0.37, 0.6, 0), V(-0.17, 0.6, 0),
      V(-0.27, 0.6, -0.1), V(-0.27, 0.6, 0.1), V(-0.27, 0.67, -0.07), V(-0.27, 0.67, 0.07), V(0.12, 0, 0.12), V(-0.3, 0, 0.32), V(-0.62, 0, 0.18)];
    function phoneHome() { return !O.root.classList.contains("checked") && stage.clientWidth <= 760 && stage.clientHeight > stage.clientWidth; }
    // a fresh check leaves the phone's first screen from where the camera is: the lamp band's view stays the first key
    // until the glide out has come to rest on a later stop, so no frame shows the old first-stop framing (q10-rel)
    var leaveHome = false;
    function pickKeys() {
      var tall = (stage.clientWidth / Math.max(1, stage.clientHeight)) < 0.85, all = KEYS[tall ? "tall" : "wide"];
      var cams = O.checked && O.NS > 1 ? O.STOPS.map(function (s) { return s.cam; }) : [0, 1, 2, 3, 4, 5];
      var wasHome = !!K && K[0] === PHONE_HOME;
      K = cams.map(function (c) { return all[c]; }); AK = cams.map(function (c) { return AIMS6[c]; }); EK = cams.map(function (c) { return EXPO6[c]; });
      if (phoneHome()) leaveHome = false;
      else if (wasHome && O.root.classList.contains("checked") && stage.clientWidth <= 760 && stage.clientHeight > stage.clientWidth) leaveHome = true;
      if (phoneHome() || leaveHome) { K[0] = PHONE_HOME; AK[0] = AIMS6[0]; EK[0] = EXPO6[0]; }
      CL = K.length - 1;
      posCurve = new T.CatmullRomCurve3(K.map(function (k) { return k.p; }), false, "centripetal");
      tgtCurve = new T.CatmullRomCurve3(K.map(function (k) { return k.t; }), false, "centripetal");
    }

    // ---- helpers -----------------------------------------------------------------
    function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
    function c01(v) { return clamp(v, 0, 1); }
    function ss(v) { v = c01(v); return v * v * (3 - 2 * v); }
    function eio(u) { u = c01(u); return u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2; }
    function eo(u) { u = c01(u); return 1 - Math.pow(1 - u, 3); }
    function hold(P) { var s = Math.floor(clamp(P, 0, CL - 0.0001)), u = P - s; return s + eio((u - 0.06) / 0.88); }
    function lerpArr(arr, hp) { var s = Math.floor(clamp(hp, 0, CL - 0.0001)), f = hp - s; return arr[s] + (arr[Math.min(CL, s + 1)] - arr[s]) * f; }
    var tmpV = new T.Vector3(), qb = new T.Quaternion(), mBasis = new T.Matrix4();
    var right = new T.Vector3(), up = new T.Vector3(), fwd = new T.Vector3(), back = new T.Vector3(), zAx = new T.Vector3();

    // ---- sizing and resolution ------------------------------------------------------
    // A still picture is always drawn at the full ratio, min(2, devicePixelRatio). Only while something moves may the
    // ratio drop, and never below 1; the first frame after the motion ends is drawn at the full ratio again.
    var W = 1, H = 1, sized = false, ratioNow = 0, lastRatio = 0, shadowDirty = true;
    function setRatio(r) { if (r !== ratioNow) { ratioNow = r; renderer.setPixelRatio(r); renderer.setSize(W, H, false); } }
    // F1: on a touch screen a change of height alone keeps the picture as it is when it is the phone keyboard (a text
    // field has focus) or the address bar sliding (160 px or less): the stage stays at the height it was drawn for,
    // running under the keyboard or the bar, so no strip is left uncovered and nothing is reallocated or reframed.
    // Anything else (a new width, a new pixel ratio, a taller window, a desktop window dragged shorter, text field or
    // not) draws afresh, once.
    function keepsPicture(w, h, d) {
      if (!sized || w !== W || d !== DPR_CAP || h > H || !O.coarse.matches) return false;
      return O.isField(document.activeElement) || H - h <= 160;
    }
    function resize() {
      var d = Math.min(2, window.devicePixelRatio || 1);
      stage.style.height = "";
      var w = stage.clientWidth, h = stage.clientHeight;
      if (keepsPicture(w, h, d)) { stage.style.height = H + "px"; return; }
      DPR_CAP = d; dpr = DPR_CAP;
      W = w; H = h; sized = true; ratioNow = 0; setRatio(dpr); forceDraw = true;
      camera.aspect = W / H; pickKeys(); O.layout(); cardKey = ""; shadowDirty = true; O.wake();
    }
    var frameTimes = [], slowFrames = [];
    function adaptMoving(ft) {
      // one very slow moving frame is enough to drop to ratio 1 for the rest of the move (the still picture is redrawn at full ratio)
      if (ft > 120 && dpr > 1) { dpr = 1; frameTimes = []; return; }
      frameTimes.push(ft); if (frameTimes.length > 10) frameTimes.shift();
      if (frameTimes.length < 10) return;
      var avg = frameTimes.reduce(function (a, b) { return a + b; }, 0) / frameTimes.length;
      if (avg > 24 && dpr > 1) { dpr = Math.max(1, dpr - 0.25); frameTimes = []; }
    }

    // ---- pointer: hover and pick sheets at the weighing stop -------------------------
    var ray = new T.Raycaster(), ndc = new T.Vector2(), pointer = { x: 0, y: 0, has: false }, Ps = 0;
    function pickAt(cx, cy) {
      var r = canvas.getBoundingClientRect();
      ndc.set(((cx - r.left) / r.width) * 2 - 1, -((cy - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      var hit = ray.intersectObjects(sheets.map(function (o) { return o.mesh; }), false)[0];
      return hit ? hit.object.userData.n : 0;
    }
    function atWeigh() { return O.WEIGH >= 0 && Math.abs(Ps - O.WEIGH) < 0.25; }
    stage.addEventListener("pointermove", function (e) {
      pointer.x = e.clientX / innerWidth - 0.5; pointer.y = e.clientY / innerHeight - 0.5; pointer.has = true; O.wake();
      if (atWeigh()) { var n = pickAt(e.clientX, e.clientY); O.hoverN = n; stage.style.cursor = n ? "pointer" : ""; }
      else stage.style.cursor = "";
    });
    stage.addEventListener("click", function (e) {
      if (!atWeigh()) return;
      var n = pickAt(e.clientX, e.clientY);
      if (n) { if (n === O.openN) O.clearStudy(); else O.selectStudy(n, { from: "scene" }); } else O.clearStudy();
      O.wake();
    });

    // ---- the frame ---------------------------------------------------------------
    var last = performance.now();
    var raf = 0, frames = 0, looping = false, wasMoving = false, waiters = [], cardKey = "", cardRectNow = null;
    O.wake = function () { if (!raf) raf = requestAnimationFrame(frame); };
    addEventListener("resize", resize);
    resize();
    Ps = O.rawProgress();

    function poseSheets(P, dt) {
      var busy = false;
      sheets.forEach(function (o, i) {
        var m = o.mesh, x, y, z, yaw, tilt = 0, roll = 0, landY;
        // the reading beat: a quick drop on the way to the answer
        var td = o.placed ? 1 : O.reduce.matches ? (P >= 0.3 ? 1 : 0) : c01((P - 0.02 - i * 0.04) / 0.26);
        var vt = c01(td / 0.62), ht = eo(td);
        x = o.from.x + (o.rest.x - o.from.x) * ht;
        z = o.from.z + (o.rest.z - o.from.z) * ht;
        y = o.rest.y + (o.from.y - o.rest.y) * (1 - vt * vt);
        yaw = o.fromYaw + (o.restYaw - o.fromYaw) * eo(td * 1.1);
        tilt = (1 - eo(vt)) * (0.32 + o.seed * 0.2); roll = (1 - eo(vt)) * (o.seed - 0.5) * 0.4;
        var visible = td > 0;
        landY = o.rest.y - PT / 2;
        // the weighing: relied on into the light, the rest to the dim side
        var tsr = O.reduce.matches ? (P >= 2.5 ? 1 : 0) : eio((P - 2.08 - o.order * 0.022) / 0.45);
        if (tsr > 0) {
          x = o.rest.x + (o.fan.x - o.rest.x) * tsr;
          z = o.rest.z + (o.fan.z - o.rest.z) * tsr;
          y = o.rest.y + (o.fan.y - o.rest.y) * tsr + Math.sin(Math.PI * tsr) * (0.03 + o.order * 0.002);
          yaw = o.restYaw + (o.fanYaw - o.restYaw) * tsr; roll = o.fanRoll * tsr;
          landY = (o.rest.y + (o.fan.y - o.rest.y) * tsr) - PT / 2;
        }
        var hv = O.hoverN === o.s.n && O.WEIGH >= 0 && Math.abs(P - O.WEIGH) < 0.4 ? 1 : 0;
        o.hover += (hv - o.hover) * c01(dt * 10);
        if (Math.abs(hv - o.hover) < 0.002) o.hover = hv; else busy = true;
        y += o.hover * 0.008;
        m.visible = visible;
        m.position.set(x, y, z);
        m.quaternion.setFromEuler(new T.Euler(tilt, yaw, roll, "YXZ"));
        // lifted toward the camera when picked
        var target = O.openN === o.s.n ? 1 : 0;
        o.lift = O.reduce.matches ? target : o.lift + (target - o.lift) * c01(dt * 5.5);
        if (Math.abs(target - o.lift) < 0.001) o.lift = target; else busy = true;
        hiRes(o, target);
        if (o.lift > 0.001) {
          var L = eio(o.lift);
          camera.getWorldDirection(fwd); right.setFromMatrixColumn(camera.matrixWorld, 0); up.setFromMatrixColumn(camera.matrixWorld, 1);
          var th = Math.tan(T.MathUtils.degToRad(camera.fov / 2)), wide = camera.aspect > 0.85;
          var d = wide ? Math.max(PH / (0.7 * 2 * th), PW / (0.3 * 2 * th * camera.aspect)) : Math.max(PH / (0.4 * 2 * th), PW / (0.8 * 2 * th * camera.aspect));
          var xN = wide ? 0.05 : 0, yN = wide ? 0 : 0.5;
          tmpV.copy(camera.position).addScaledVector(fwd, d).addScaledVector(right, xN * d * th * camera.aspect).addScaledVector(up, yN * d * th);
          back.copy(fwd).negate(); zAx.crossVectors(right, back);
          mBasis.makeBasis(right, back, zAx); qb.setFromRotationMatrix(mBasis);
          m.position.lerp(tmpV, L); m.quaternion.slerp(qb, L);
        }
        var h = Math.max(0, m.position.y - PT / 2 - landY);
        var shScale = 1 + h * 2.2, shOp = visible ? 0.55 * (1 - c01(h * 7)) : 0;
        o.sh.position.set(m.position.x, landY + 0.0003, m.position.z);
        o.sh.rotation.z = yaw; o.sh.scale.set(shScale, shScale, 1);
        o.sh.material.opacity = shOp * (1 - (o.lift > 0.001 ? eio(o.lift) : 0));
        o.sh.visible = shOp > 0.003;
        m.userData.face.emissiveIntensity = 0.62 * (o.lift > 0.001 ? eio(o.lift) : 0);
      });
      return busy;
    }
    function poseCard(P) {
      // rest (blank up) -> rises and stands facing the answer camera -> laid down, verdict up, beside the pencil
      var a = O.reduce.matches ? (P >= 0.5 ? 1 : 0) : c01((P - 0.3) / 0.55);
      var b = O.reduce.matches ? (P >= 1.5 ? 1 : 0) : c01((P - 1.12) / 0.6);
      var cp = new T.Vector3(), cq = new T.Quaternion(), stand = 0;
      if (b <= 0) {
        var la = eio(a), ra = eio((a - 0.1) / 0.9);
        cp.copy(CARD_REST).lerp(CARD_STAND, la); cp.y += Math.sin(Math.PI * la) * 0.06;
        cq.copy(Q_REST_BLANK).slerp(Q_STAND, ra); stand = ra;
      } else {
        var lb = eio(b);
        cp.copy(CARD_STAND).lerp(CARD_DOWN, lb); cp.y += Math.sin(Math.PI * lb) * 0.05;
        cq.copy(Q_STAND).slerp(Q_REST_VERDICT, eio((b - 0.05) / 0.9)); stand = 1 - eio((b - 0.05) / 0.9);
      }
      card.position.copy(cp); card.quaternion.copy(cq);
      // standing, the face is plain paper only while the HTML words are fully shown over it; whenever they are not
      // (fading, scrolled, a panel passing), the card carries its printed face, so one of the two is always readable
      standing = b <= 0.35;
      var fm = standing ? standFace() : cvMap; if (vMat.map !== fm) { vMat.map = fm; vMat.emissiveMap = fm; }
      // standing, it drops its hard cast shadow for a soft contact one
      cardBox.castShadow = stand < 0.3;
      var bottom = cp.y - (CARD_H / 2) * stand - CARD_T / 2 * (1 - stand);
      var h = Math.max(0, bottom);
      cardSh.position.set(cp.x, 0.00025, cp.z + 0.012 * stand);
      var yawNow = b > 0 ? CARD_YAW * eio((b - 0.05) / 0.9) : CARD_YAW * (1 - stand);
      cardSh.rotation.z = yawNow;
      var sc = 1 + h * 3; cardSh.scale.set(sc * (1 + 0.15 * stand), sc * (1 - 0.55 * stand), 1);
      cardSh.material.opacity = 0.8 * (1 - c01(h * 14)) * (1 - 0.15 * stand);
      vMat.emissiveIntensity = 0.1 * stand;
      return stand;
    }
    function frame(now) {
      raf = 0;
      var fStart = performance.now();
      // slow frames take bigger steps, so a slow machine settles in a few frames, not dozens
      var ft = now - last, dt = wasMoving ? Math.min(1, ft / 1000) : 1 / 60; last = now;
      if (O.tween) O.tween(now);
      var P = O.rawProgress();
      if (O.reduce.matches) Ps = Math.round(P);
      else Ps += (P - Ps) * (1 - Math.exp(-dt * 4.2));
      if (O.slowGl || Math.abs(P - Ps) < 0.0015) Ps = P;
      // at rest on a later stop's key, or anywhere past the second stop, the picture does not depend on the first key,
      // so it can return to the result's own
      if (leaveHome && Ps === P && (P >= 2 || (P >= 1 && Math.abs(P - Math.round(P)) < 1e-6))) { leaveHome = false; pickKeys(); }

      var hp = O.reduce.matches ? Math.round(P) : hold(Ps), u = CL ? hp / CL : 0;
      posCurve.getPoint(u, camera.position); var tg = tgtCurve.getPoint(u);
      var s = Math.floor(clamp(hp, 0, CL - 0.0001)), f = hp - s, A = K[s], B = K[Math.min(CL, s + 1)];
      camera.fov = A.fov + (B.fov - A.fov) * f;
      var fx = A.fx + (B.fx - A.fx) * f, fy = A.fy + (B.fy - A.fy) * f;
      if (!O.reduce.matches && pointer.has && camera.aspect > 0.85) { camera.position.x += pointer.x * 0.03; camera.position.y -= pointer.y * 0.015; }
      camera.lookAt(tg);
      camera.setViewOffset(W, H, (0.5 - fx) * W, (0.5 - fy) * H, W, H);
      camera.updateProjectionMatrix();
      camera.updateMatrixWorld();

      var busy = poseSheets(Ps, dt);
      var stand = poseCard(Ps);
      var k1 = ss((Ps - 0.35) / 0.5) * (1 - ss((Ps - 1.12) / 0.45));
      key.intensity = 1.5 * k1 * stand;
      tmpV.copy(AK[s]).lerp(AK[Math.min(CL, s + 1)], f); aimLamp(tmpV);
      spot.angle = LAMP_ANGLE - 0.26 * k1;
      renderer.toneMappingExposure = EXPOSURE * lerpArr(EK, hp);
      var moving = !!O.tween || P !== Ps || busy;
      if (moving && wasMoving) adaptMoving(ft);
      if (!moving) { dpr = DPR_CAP; frameTimes = []; }
      setRatio(dpr);
      // shadows are redrawn only when something that casts one has moved
      // the shadow map is redrawn only when something that casts a shadow has moved, appeared or gone (not for a camera move)
      var sig = casterSig();
      if (shadowDirty || sig !== lastSig) { renderer.shadowMap.needsUpdate = true; lastSig = sig; shadowDirty = false; }
      if (placeCard(stand, moving, ft)) moving = true;
      // a frame that would draw the very picture already on the canvas (a scroll of the home page, a wake with nothing
      // new) is not drawn again: the canvas keeps its last frame
      var vk = viewKey();
      if (forceDraw || vk !== drawnKey) {
        renderer.render(scene, camera); drawnKey = vk; forceDraw = false; lastRatio = ratioNow; frames++;
        // a machine drawing under three frames a second glides by cuts. Judged on the last three frames drawn (a moving
        // frame by the time since the one before, a still one by its own cost), so one frame held up by other work (an
        // answer being laid out) never turns every later glide into a cut, and a cut frame drawn quickly lifts it
        slowFrames.push((wasMoving ? ft : performance.now() - fStart) > 350); if (slowFrames.length > 3) slowFrames.shift();
        O.slowGl = slowFrames.length === 3 && slowFrames[0] && slowFrames[1] && slowFrames[2];
      }
      wasMoving = moving; looping = moving;
      if (frames === 1) setTimeout(paintLazy, 0);
      if (moving) O.wake();
      else {
        if (woodState !== "loading" && !handed) {
          // never under a finger or mid scroll: the swap waits for 300 ms of rest
          var quiet = performance.now() - O.lastScrollAt;
          if (O.touching || O.pressing || quiet < 300) { clearTimeout(O.handT); O.handT = setTimeout(O.wake, O.touching || O.pressing ? 120 : 310 - quiet); return; }
          handover(); O.wake(); return;
        }
        if (woodState !== "loading" && !readyDone) { readyDone = true; requestAnimationFrame(function () { O.readyResolve(); }); }
        if (waiters.length) { var w = waiters; waiters = []; requestAnimationFrame(function () { w.forEach(function (r) { r(); }); }); }
        if (readyDone) warmSoon();
      }
    }
    var lastShadowPs = -1, readyDone = false, lastSig = "", casters = null, handed = false;
    // everything the picture is drawn from: the camera, every caster (lastSig), the lights, the exposure, the faces and
    // their textures, the soft shadows and the canvas size. forceDraw covers anything else (a resize clears the canvas)
    var drawnKey = "", forceDraw = true;
    function texKey(t) { return t ? t.id + "." + t.version : "0"; }
    function viewKey() {
      var e = camera.matrixWorld.elements, p = camera.projectionMatrix.elements, out = [];
      for (var i = 0; i < 16; i++) out.push(e[i].toFixed(6), p[i].toFixed(6));
      sheets.forEach(function (o) { var f = o.mesh.userData.face; out.push(texKey(f.map), f.emissiveIntensity.toFixed(4), o.sh.visible ? o.sh.material.opacity.toFixed(4) : "-"); });
      out.push(texKey(vMat.map), vMat.emissiveIntensity.toFixed(4), texKey(woodMat.map), woodMat.color.getHex(), woodMat.version, cardSh.material.opacity.toFixed(4),
        renderer.toneMappingExposure.toFixed(5), key.intensity.toFixed(5), spot.angle.toFixed(5), ratioNow, W, H, lastSig);
      return out.join(",");
    }
    // the desk takes over from the early text-mode card: the page switches to its desk layout and keeps the reader on the same stop
    function handover() {
      handed = true;
      var y = window.scrollY, P = O.checked ? O.rawProgress() : 0, k = Math.floor(P + 1e-6), off = y - O.anchors[Math.min(O.LAST, k)];
      // inside Weighed or Research: the first row or paragraph whose top is on screen keeps its place on screen
      var keep = null, keepTop = 0;
      if (O.checked && P - k < 1e-6 && O.sections[k].classList.contains("long") && off > 0) {
        var cands = O.sections[k].querySelectorAll(".row, p, h3, summary");
        for (var i = 0; i < cands.length; i++) { var q = cands[i].getBoundingClientRect(); if (q.height && q.top >= O.stripH && q.top < innerHeight - 80) { keep = cands[i]; keepTop = q.top; break; } }
      }
      O.root.classList.remove("early"); O.root.classList.add("has-gl");
      O.layout();
      if (O.checked) {
        var to = P - k < 1e-6 ? Math.min(O.holds[k], O.anchors[k] + Math.max(0, off)) : O.holds[k] + (P - k) * (O.anchors[k + 1] - O.holds[k]);
        if (keep) to = Math.max(O.anchors[k], Math.min(O.holds[k], window.scrollY + keep.getBoundingClientRect().top - keepTop));
        window.scrollTo({ top: Math.round(to), behavior: "instant" });
        O.restY = O.prevY = O.progY = window.scrollY; O.runLen = 0;
      }
      Ps = O.rawProgress(); cardKey = ""; O.updateWay();
    }
    function casterSig() {
      if (!casters) { casters = []; scene.traverse(function (o) { if (o.castShadow && o.isMesh) casters.push(o); }); }
      scene.updateMatrixWorld();
      var h = 0, h2 = 0;
      for (var i = 0; i < casters.length; i++) {
        var o = casters[i], vis = o.visible; for (var q = o.parent; vis && q; q = q.parent) vis = q.visible;
        if (!vis) { h2 += (i + 1) * 7919; continue; }
        var e = o.matrixWorld.elements; h += (e[0] + e[2] * 3 + e[5] * 5 + e[9] * 7 + e[12] * 11 + e[13] * 13 + e[14] * 17) * (i + 1);
      }
      return h.toFixed(6) + "|" + h2;
    }
    setTimeout(function () { if (woodState === "loading") { woodState = "slow"; O.wake(); } }, 6000);

    // ---- the answer card: real HTML text laid exactly over the settled 3D card ---------
    var vcard = document.getElementById("vcard"), shortLabel = document.getElementById("short-label");
    var cardCorners = [V(-CARD_W / 2, CARD_H / 2, CARD_T / 2), V(CARD_W / 2, CARD_H / 2, CARD_T / 2), V(CARD_W / 2, -CARD_H / 2, CARD_T / 2), V(-CARD_W / 2, -CARD_H / 2, CARD_T / 2)];
    var cardA = 0, capInner = document.querySelector("#cap-answer .inner");
    function setCardA(a) { cardA = a; vcard.style.opacity = shortLabel.style.opacity = a ? a.toFixed(3) : ""; }
    function placeCard(stand, moving, ft) {
      if (!handed) { setStandFace(); return false; }
      // words appear only on a settled card, and stay until the card has clearly left (no flicker between frames)
      var settled = cardRectNow ? (O.curStop === 1 && Math.abs(Ps - 1) < 0.02 && stand > 0.98) : (!moving && O.curStop === 1 && Math.abs(Ps - 1) < 0.003 && stand > 0.995);
      if (!settled && cardA === 0) { if (cardRectNow) cardRectNow = null; vcard.classList.remove("on"); shortLabel.classList.remove("on"); setStandFace(); return false; }
      // while the words fade, in or out, they stay laid over the card
      card.updateMatrixWorld(true);
      var c = cardCorners.map(function (v) { var p = v.clone().applyMatrix4(cardBox.matrixWorld).project(camera); return [(p.x * 0.5 + 0.5) * W, (-p.y * 0.5 + 0.5) * H]; });
      var x0 = Math.ceil(Math.max(c[0][0], c[3][0])), x1 = Math.floor(Math.min(c[1][0], c[2][0]));
      var y0 = Math.ceil(Math.max(c[0][1], c[1][1])), y1 = Math.floor(Math.min(c[2][1], c[3][1]));
      var r = { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, k = [r.x, r.y, r.w, r.h].join(",");
      // on a short screen the panel scrolls up over the held card: the words leave before the panel reaches them
      if (settled && capInner && capInner.getBoundingClientRect().top < r.y + r.h + 10 && capInner.getBoundingClientRect().left < r.x + r.w + 10) settled = false;
      if (!settled && cardRectNow) cardRectNow = null;
      if (!settled && cardA === 0) { vcard.classList.remove("on"); shortLabel.classList.remove("on"); setStandFace(); return false; }
      if (k !== cardKey) {
        cardKey = k;
        vcard.style.left = r.x + "px"; vcard.style.top = r.y + "px"; vcard.style.width = Math.max(0, r.w) + "px"; vcard.style.height = Math.max(0, r.h) + "px";
        if (settled) layoutCard(r.w, r.h);
        shortLabel.style.left = r.x + "px"; shortLabel.style.top = Math.max(0, r.y - shortLabel.offsetHeight - 12) + "px";
      }
      if (settled) { cardRectNow = r; vcard.classList.add("on"); shortLabel.classList.add("on"); }
      var step = O.reduce.matches ? 1 : Math.min(0.15, Math.max(0.004, (ft || 16) / 1000)) / 0.15;
      setCardA(settled ? Math.min(1, cardA + step) : Math.max(0, cardA - step));
      setStandFace();
      if (cardA === 0) { vcard.classList.remove("on"); shortLabel.classList.remove("on"); }
      return settled ? cardA < 1 : cardA > 0;
    }
    var standing = false, csvMap = null, csvKey = "", csvFontOk = false;
    function standFace() { return cardA >= 1 && cardRectNow ? csMap : (csvMap || cvMap); }
    function setStandFace() { if (!standing) return; var fm = standFace(); if (vMat.map !== fm) { vMat.map = fm; vMat.emissiveMap = fm; } }
    // each line of an element's text, as the browser set it: its words, left edge and top
    function textLines(n) {
      var out = [], cur = null, w = document.createTreeWalker(n, NodeFilter.SHOW_TEXT), t, rg = document.createRange(), m, re = /\S+/g;
      while ((t = w.nextNode())) {
        re.lastIndex = 0;
        while ((m = re.exec(t.data))) {
          rg.setStart(t, m.index); rg.setEnd(t, m.index + m[0].length); var q = rg.getClientRects()[0]; if (!q) continue;
          if (!cur || Math.abs(q.top - cur.top) > 2) { cur = { t: m[0], x: q.left, top: q.top }; out.push(cur); } else cur.t += " " + m[0];
        }
      }
      return out;
    }
    function paintStandFace(r) {
      var key = [Math.round(r.w), Math.round(r.h), vcard.className, vcard.querySelector(".v").style.fontSize, vcard.querySelector(".lbl").style.display, fontsOk(), vcard.textContent].join(",");
      if (key === csvKey || r.w < 40) return; csvKey = key;
      var cw = Math.min(2048, Math.max(1024, Math.round(r.w * DPR_CAP * 1.25))), ch = Math.round(cw * CH / CW);
      var c = cnv(cw, ch); paintCard(c, false, cw / CW);
      var x = c.getContext("2d"), vb = vcard.getBoundingClientRect();
      x.setTransform(cw / r.w, 0, 0, ch / r.h, 0, 0); x.textBaseline = "alphabetic";
      [].forEach.call(vcard.children, function (n) {
        var cs = getComputedStyle(n); if (cs.display === "none") return;
        x.font = cs.fontStyle + " " + cs.fontWeight + " " + cs.fontSize + " " + cs.fontFamily;
        if ("letterSpacing" in x) x.letterSpacing = cs.letterSpacing === "normal" ? "0px" : cs.letterSpacing;
        x.fillStyle = cs.color;
        var asc = x.measureText("Hg").fontBoundingBoxAscent, up = cs.textTransform === "uppercase";
        // a clamped block paints only the lines it shows, the last ending in an ellipsis; the claim carries its quotes
        var lines = textLines(n), cap = parseInt(cs.webkitLineClamp, 10), quoted = n.classList.contains("q");
        if (cap > 0 && lines.length > cap) { lines = lines.slice(0, cap); lines[cap - 1] = { t: lines[cap - 1].t.replace(/[\s,.;:]+$/, "") + "\u2026", x: lines[cap - 1].x, top: lines[cap - 1].top }; }
        else if (quoted && lines.length) lines[lines.length - 1] = { t: lines[lines.length - 1].t + "\u201D", x: lines[lines.length - 1].x, top: lines[lines.length - 1].top };
        lines.forEach(function (L, i) {
          var dx = quoted && i === 0 ? x.measureText("\u201C").width : 0;
          x.fillText((dx ? "\u201C" : "") + (up ? L.t.toUpperCase() : L.t), L.x - vb.left - dx, L.top - vb.top + asc);
        });
      });
      var old = csvMap; csvMap = tex(c); csvFontOk = fontsOk(); if (vMat.map === old) { vMat.map = vMat.emissiveMap = csvMap; } if (old) old.dispose();
    }
    // lay the HTML card out for the Answer stop's card and paint the printed face from it
    function prepareStandFace() {
      if (!handed || !O.checked || !cardPainted) return;
      var r = cardRectAt(1);
      r = { x: Math.ceil(r.x), y: Math.ceil(r.y), w: Math.floor(r.x + r.w) - Math.ceil(r.x), h: Math.floor(r.y + r.h) - Math.ceil(r.y) };
      vcard.style.left = r.x + "px"; vcard.style.top = r.y + "px"; vcard.style.width = r.w + "px"; vcard.style.height = r.h + "px";
      layoutCard(r.w, r.h); paintStandFace(r); cardKey = "";
    }
    // tall screens: the card sits just under its label, and the panel starts at least 12 px below the card
    var capAnswer = document.getElementById("cap-answer");
    function cardRectAt(k) {
      var A = K[k], cam = camera.clone();
      cam.position.copy(A.p); cam.fov = A.fov; cam.aspect = W / H; cam.lookAt(A.t);
      cam.setViewOffset(W, H, (0.5 - A.fx) * W, (0.5 - A.fy) * H, W, H); cam.updateProjectionMatrix(); cam.updateMatrixWorld();
      var m = new T.Matrix4().compose(CARD_STAND, Q_STAND, new T.Vector3(1, 1, 1));
      var c = cardCorners.map(function (v) { var q = v.clone().applyMatrix4(m).project(cam); return [(q.x * 0.5 + 0.5) * W, (-q.y * 0.5 + 0.5) * H]; });
      var y0 = Math.max(c[0][1], c[1][1]), y1 = Math.min(c[2][1], c[3][1]), x0 = Math.max(c[0][0], c[3][0]), x1 = Math.min(c[1][0], c[2][0]);
      return { x: x0, y: y0, h: y1 - y0, w: x1 - x0 };
    }
    // L2: where a set of world points falls on screen through a framing, as a box
    function boxAt(A, pts) {
      var cam = camera.clone();
      cam.position.copy(A.p); cam.fov = A.fov; cam.aspect = W / H; cam.lookAt(A.t);
      cam.setViewOffset(W, H, (0.5 - A.fx) * W, (0.5 - A.fy) * H, W, H); cam.updateProjectionMatrix(); cam.updateMatrixWorld();
      var b = { x0: 1e9, y0: 1e9, x1: -1e9, y1: -1e9 };
      pts.forEach(function (v) { var q = v.clone().project(cam), x = (q.x * 0.5 + 0.5) * W, y = (-q.y * 0.5 + 0.5) * H;
        b.x0 = Math.min(b.x0, x); b.x1 = Math.max(b.x1, x); b.y0 = Math.min(b.y0, y); b.y1 = Math.max(b.y1, y); });
      return b;
    }
    // L2: the lamp and its lit wood fill the band above the words, centred, with a margin all round
    var s1Inner = document.querySelector("#s1 .inner");
    function fitPhoneHome() {
      if (K[0] !== PHONE_HOME || !s1Inner) return;
      var strip = O.byId("strip"), top = (strip ? strip.offsetHeight : 32) + 16;
      var bottom = s1Inner.getBoundingClientRect().top + window.scrollY - 20, wantH = bottom - top, wantW = W - 40;
      if (wantH < 60) return;
      for (var j = 0; j < 4; j++) {
        var b = boxAt(PHONE_HOME, LAMP_PTS), k = Math.max((b.y1 - b.y0) / wantH, (b.x1 - b.x0) / wantW);
        PHONE_HOME.fov = T.MathUtils.radToDeg(2 * Math.atan(Math.tan(T.MathUtils.degToRad(PHONE_HOME.fov / 2)) * k));
        b = boxAt(PHONE_HOME, LAMP_PTS);
        PHONE_HOME.fx += (W / 2 - (b.x0 + b.x1) / 2) / W; PHONE_HOME.fy += ((top + bottom) / 2 - (b.y0 + b.y1) / 2) / H;
      }
    }
    O.layoutHook = function () {
      capAnswer.style.paddingTop = "";
      if (!K) return;
      fitPhoneHome();
      if (K[1].fx0 === undefined) K[1].fx0 = K[1].fx;
      K[1].fx = K[1].fx0;
      if (camera.aspect >= 0.85) {
        // the card comes up to just under the strip and its label, so the answer is read before the scenery
        if (K[1].fy0 === undefined) K[1].fy0 = K[1].fy;
        K[1].fy = K[1].fy0;
        if (!K[1].fov0) K[1].fov0 = K[1].fov;
        K[1].fov = K[1].fov0;
        var wantY = O.stripH + 12 + (shortLabel.offsetHeight || 16) + 10;
        for (var a = 0; a < 3; a++) { var qa = cardRectAt(1); K[1].fy += (wantY - qa.y) / H; }
        // wide screens: the card keeps 32 px clear of the panel beside it (at 1280 wide the panel would otherwise reach over its right edge)
        var inner = capAnswer.querySelector(".inner"), pl = inner ? inner.getBoundingClientRect().left : W;
        for (var m = 0; m < 3; m++) { var q = cardRectAt(1), over = q.x + q.w - (pl - 32); if (over <= 0) break; K[1].fx -= Math.min(over, Math.max(0, q.x - 24)) / W; }
        // the panel now starts level with the card: where the card still reaches it, the card is drawn smaller to fit beside it
        var qw = cardRectAt(1), wantW = pl - 32 - 24;
        if (qw.x + qw.w > pl - 32 && wantW > 200) {
          for (var f = 0; f < 3; f++) { var qf = cardRectAt(1), tf = Math.tan(T.MathUtils.degToRad(K[1].fov / 2)) * qf.w / wantW; K[1].fov = T.MathUtils.radToDeg(2 * Math.atan(tf)); }
          for (var b = 0; b < 3; b++) { var qb = cardRectAt(1); K[1].fy += (wantY - qb.y) / H; }
          for (var m2 = 0; m2 < 3; m2++) { var q2 = cardRectAt(1), over2 = q2.x + q2.w - (pl - 32); if (over2 <= 0) break; K[1].fx -= Math.min(over2, Math.max(0, q2.x - 24)) / W; }
        }
        cardKey = ""; prepareStandFace(); return;
      }
      // every phone draws the card at the same width, so its words always fit it; the panel starts under its real bottom edge
      var want = O.stripH + 12 + (shortLabel.offsetHeight || 16) + 10, wantW = Math.min(W - 22, 560);
      if (!K[1].fov0) K[1].fov0 = K[1].fov;
      K[1].fov = K[1].fov0;
      for (var j = 0; j < 3; j++) { var q = cardRectAt(1), th = Math.tan(T.MathUtils.degToRad(K[1].fov / 2)) * q.w / wantW; K[1].fov = T.MathUtils.radToDeg(2 * Math.atan(th)); }
      for (var i = 0; i < 3; i++) { var r = cardRectAt(1); K[1].fy += (want - r.y) / H; }
      r = cardRectAt(1);
      var capTop = capAnswer.getBoundingClientRect().top - O.byId("s2").getBoundingClientRect().top;
      capAnswer.style.paddingTop = Math.max(0, Math.ceil(r.y + r.h + 14 - capTop)) + "px";
      cardKey = ""; prepareStandFace();
    };
    // where a line's baseline falls inside its box, measured with a zero-size marker
    function baseOff(node) {
      var m = document.createElement("span"); m.style.cssText = "display:inline-block;width:0;height:0;vertical-align:baseline";
      node.insertBefore(m, node.firstChild); var off = m.getBoundingClientRect().top - node.getBoundingClientRect().top; node.removeChild(m); return off;
    }
    // the painted card's own layout (1600 by 960 units): label, claim, verdict, short answer, each at its baseline
    var CARD_LAYOUT = [
      { sel: ".lbl", size: 32, min: 13, left: 96, base: 108 },
      { sel: ".q", size: 54, min: 17, left: 96, base: 252 },
      { sel: ".v", size: 200, min: 40, left: 86, base: 490 },
      { sel: ".t", size: 54, min: 17, left: 96, base: 618, lh: 70, width: 1408 }
    ];
    // the claim, the verdict and the short answer fitted to the card as paintCard fits them
    function linesOf(n) { var lh = parseFloat(getComputedStyle(n).lineHeight) || 1; return Math.max(1, Math.round(n.offsetHeight / lh)); }
    function place(n, base, sc) { n.style.top = "0px"; n.style.top = (base * sc - baseOff(n)).toFixed(2) + "px"; }
    function clampTo(n, k) { n.style.display = "-webkit-box"; n.style.webkitBoxOrient = "vertical"; n.style.webkitLineClamp = String(k); n.style.overflow = "hidden"; }
    function fitCardWords(sc) {
      var q = vcard.querySelector(".q"), v = vcard.querySelector(".v"), t = vcard.querySelector(".t");
      q.style.whiteSpace = "normal"; q.style.width = (1408 * sc).toFixed(2) + "px";
      var cf = fitClaim(function (size) { q.style.fontSize = Math.max(13, size * sc).toFixed(2) + "px"; q.style.lineHeight = "1.2"; return linesOf(q); });
      q.style.fontSize = Math.max(13, cf.size * sc).toFixed(2) + "px";
      if (cf.clamp) clampTo(q, 3);
      place(q, claimBase(cf), sc);
      var vw = v.scrollWidth, room = VERDICT_W * sc;
      if (vw > room) { v.style.fontSize = (parseFloat(v.style.fontSize) * room / vw).toFixed(2) + "px"; place(v, 490, sc); }
      var tf = fitTldr(function (size) { t.style.fontSize = Math.max(13, size * sc).toFixed(2) + "px"; t.style.lineHeight = (Math.round(size * 1.3) * sc).toFixed(2) + "px"; return linesOf(t); });
      t.style.fontSize = Math.max(13, tf.size * sc).toFixed(2) + "px"; t.style.lineHeight = (tf.lh * sc).toFixed(2) + "px";
      if (linesOf(t) > tf.lines) clampTo(t, tf.lines);
      place(t, 618, sc);
    }
    function layoutCard(w, h) {
      var sc = w / 1600, compact = 54 * sc < 17;
      vcard.classList.toggle("compact", compact);
      CARD_LAYOUT.forEach(function (L) {
        var n = vcard.querySelector(L.sel); n.style.cssText = "";
        if (compact) return;
        n.style.fontSize = Math.max(L.min, L.size * sc).toFixed(2) + "px";
        n.style.lineHeight = L.lh ? (L.lh * sc).toFixed(2) + "px" : "1.2";
        n.style.left = (L.left * sc).toFixed(2) + "px";
        if (L.width) n.style.width = (L.width * sc).toFixed(2) + "px";
        n.style.top = "0px";
        n.style.top = (L.base * sc - baseOff(n)).toFixed(2) + "px";
      });
      if (!compact) fitCardWords(sc);
      if (compact) {
        // a phone: the same four lines in flow, at reading sizes, shrinking the verdict until all of it fits on the card
        var v = vcard.querySelector(".v"), vs = Math.min(48, Math.round(w * 0.12));
        v.style.fontSize = vs + "px";
        while (vcard.scrollHeight > h + 0.5 && vs > 32) { vs -= 2; v.style.fontSize = vs + "px"; }
        // the smallest cards drop the label line, which the painted card already carries, before the verdict goes below 28 px
        if (vcard.scrollHeight > h + 0.5) vcard.querySelector(".lbl").style.display = "none";
        while (vcard.scrollHeight > h + 0.5 && vs > 28) { vs -= 2; v.style.fontSize = vs + "px"; }
        // a long claim on the smallest phones gives up lines before the short answer loses any: two, then one, ending in an
        // ellipsis (the whole claim is the page's heading and on the printed face's twin in text mode)
        var q = vcard.querySelector(".q");
        if (vcard.scrollHeight > h + 0.5) q.style.webkitLineClamp = "2";
        if (vcard.scrollHeight > h + 0.5) q.style.webkitLineClamp = "1";
      }
    }

    // ---- the lifted sheet is repainted at twice the size, one at a time --------------
    var hiSlot = null;
    function hiRes(o, target) {
      if (target === 1 && hiSlot !== o) {
        if (hiSlot) dropHi();
        var c = cnv(SW * 2, SH * 2); paintSheet(c, o.s, o.seed, 2);
        var t = tex(c); o.lowMap = o.mesh.userData.face.map;
        o.mesh.userData.face.map = t; o.mesh.userData.face.emissiveMap = t; hiSlot = o;
      } else if (target === 0 && hiSlot === o && o.lift === 0) dropHi();
    }
    function dropHi() {
      var o = hiSlot, f = o.mesh.userData.face, t = f.map; f.map = o.lowMap; f.emissiveMap = o.lowMap; t.dispose(); hiSlot = null;
    }

    // ---- the browser drops the drawing context (a phone backgrounding the tab, low memory) ----
    // The canvas goes blank, and the card's dark words would sit on the dark page. The page goes to
    // the text mode at once, on the same stop, and stays there: no frame is drawn again.
    var glLost = false;
    canvas.addEventListener("webglcontextlost", function () {
      if (glLost) return; glLost = true;
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      looping = false; O.scene3d = false;
      O.wake = function () {}; O.drawImpl = function () {}; O.layoutHook = function () {};
      O.onLoad = function () {}; O.onHome = function () {}; O.onStage = function () {};
      // the first row or paragraph on screen in the stop the reader is on keeps its place on screen
      var k = O.curStop, keep = null, keepTop = 0, cands = O.sections[k] ? O.sections[k].querySelectorAll(".row, p, h3, summary") : [];
      for (var i = 0; i < cands.length; i++) { var q = cands[i].getBoundingClientRect(); if (q.height && q.top >= O.stripH && q.top < innerHeight - 80) { keep = cands[i]; keepTop = q.top; break; } }
      O.endTween();
      // the card and its label leave the desk's layout: no position, size or type set for the painted card stays
      vcard.classList.remove("on", "compact"); shortLabel.classList.remove("on");
      [vcard, shortLabel, capAnswer, stage].concat([].slice.call(vcard.querySelectorAll("[style]"))).forEach(function (n) { n.removeAttribute("style"); });
      O.fallback();
      var to = keep ? window.scrollY + keep.getBoundingClientRect().top - keepTop : O.anchors[Math.min(O.LAST, k)];
      window.scrollTo({ top: Math.max(0, Math.round(to)), behavior: "instant" });
      O.prevY = O.progY = O.restY = O.landY = window.scrollY; O.nextWords(Math.min(O.LAST, k), window.scrollY); O.noteRest(); O.updateWay();
    }, false);

    // ---- the test hook ----------------------------------------------------------------
    O.statsImpl = function () {
      O.updateWay(); return { mode: "3d", checked: O.checked, stop: O.curStop + 1, stops: O.NS, pixelRatio: lastRatio, fullPixelRatio: DPR_CAP, fog: !!scene.fog, looping: looping, frames: frames,
        cardRect: cardRectNow ? { x: cardRectNow.x, y: cardRectNow.y, w: cardRectNow.w, h: cardRectNow.h } : null, sheets: sheets.length, wood: woodState,
        // true once every printed card face in use was painted after the web fonts were ready
        facesFontsOk: (!cardPainted || cvFontOk) && (!csvMap || csvFontOk) };
    };
    O.drawImpl = function () { if (raf) { cancelAnimationFrame(raf); raf = 0; } forceDraw = true; frame(performance.now()); };
    O.gotoImpl = function (n) {
      n = clamp(Math.round(n), 1, O.NS); if (n >= 2) O.check(); O.endTween(); O.clearStudy(); O.layout();
      window.scrollTo({ top: O.anchors[n - 1], behavior: "instant" }); O.prevY = O.progY = window.scrollY; O.nextWords(n - 1, window.scrollY); O.noteRest();
      Ps = O.rawProgress(); O.updateWay();
      sheets.forEach(function (o) { o.lift = O.openN === o.s.n ? 1 : 0; });
      return new Promise(function (res) { waiters.push(res); O.wake(); });
    };
    O.sheetPointImpl = function (n) {
      var o = sheets.filter(function (q) { return q.s.n === n; })[0]; if (!o || !o.mesh.visible) return null;
      o.mesh.updateMatrixWorld(true); var r = canvas.getBoundingClientRect();
      var cand = [[0, 0], [0.25, 0.25], [-0.25, 0.25], [0.25, -0.25], [-0.25, -0.25], [0, 0.4], [0, -0.4], [0.4, 0], [-0.4, 0], [0.4, 0.4], [-0.4, -0.4], [0.4, -0.4], [-0.4, 0.4]];
      for (var i = 0; i < cand.length; i++) {
        var v = V(cand[i][0] * PW, PT / 2, cand[i][1] * PH).applyMatrix4(o.mesh.matrixWorld).project(camera);
        var x = r.left + (v.x * 0.5 + 0.5) * W, y = r.top + (-v.y * 0.5 + 0.5) * H;
        if (x < 2 || y < 2 || x > innerWidth - 2 || y > innerHeight - 2) continue;
        var top = document.elementFromPoint(x, y);
        if (pickAt(x, y) === n && top && (top === canvas || top === stage)) return { x: Math.round(x), y: Math.round(y) };
      }
      return null;
    };
    window.__evd = {
      ready: true, gl: true, renderer: renderer, scene: scene, camera: camera,
      snap: function () { Ps = O.rawProgress(); sheets.forEach(function (o) { o.lift = O.openN === o.s.n ? 1 : 0; }); O.wake(); },
      dpr: function () { return lastRatio; },
      warmed: function () { return warmGen === sheetGen; },
      open: function (n) { O.selectStudy(n, { from: "scene" }); O.wake(); },
      KEYS: KEYS, V: V, resize: resize, sheets: sheets, FAN: FAN,
      proj: function (x, y, z) { var v = new T.Vector3(x, y, z).project(camera); return [Math.round((v.x * 0.5 + 0.5) * W), Math.round((-v.y * 0.5 + 0.5) * H)]; }
    };
    // ---- lazy painting: the sheets' own faces after the first frame, a few at a time; the card on check ----
    var lazyQ = null, bigTop = false;
    function paintOne(o, big) {
      if (o.painted && !big) return; o.painted = true;
      var k = big ? 1 : 0.5, c = cnv(SW * k, SH * k); paintSheet(c, o.s, o.seed, k);
      var t = tex(c), f = o.mesh.userData.face;
      if (hiSlot === o) o.lowMap = t; else { f.map = t; f.emissiveMap = t; f.needsUpdate = true; }
    }
    function paintLazy() {
      if (lazyQ && lazyQ.gen === sheetGen) return;
      var gen = sheetGen;
      var q = lazyQ = sheets.slice().sort(function (a, b) { return b.rest.y - a.rest.y; });
      q.gen = gen;
      (function step() {
        if (lazyQ !== q) return;
        if (gen !== sheetGen) { lazyQ = null; return; }
        var t0 = performance.now();
        var seen = false;
        while (lazyQ.length && performance.now() - t0 < 12) {
          var o = lazyQ.shift(), big = bigTop && o === sheets[sheets.length - 1];
          if (big) { bigTop = false; o.painted = false; }
          paintOne(o, big); seen = seen || o.mesh.visible;
        }
        // a sheet out of sight needs no new frame
        if (seen) O.wake();
        if (lazyQ.length) { setTimeout(step, 16); return; }
        lazyQ = null;
        // faces painted after the warm-up went through are sent too
        if (warmGen === gen) { warmGen = -1; warmSoon(); }
      })();
    }
    // ---- the warm-up: the first press of Next pays for no compile and no upload ----------
    // Once an answer is readable and the picture is still, the programs the scene uses are compiled and each texture
    // is sent to the GPU, one piece per idle slice. Never mid-move, never while the reader types, scrolls, touches or
    // presses (it waits and tries again). A press before it is done finds the scene as before; nothing drawn changes.
    var warmGen = -1, warmQ = null, warmT = 0, inputAt = 0;
    addEventListener("keydown", function () { inputAt = performance.now(); }, true);
    addEventListener("input", function () { inputAt = performance.now(); }, true);
    var idle = window.requestIdleCallback ? function (f) { requestIdleCallback(f, { timeout: 1000 }); } : function (f) { setTimeout(f, 50); };
    function warmLater(ms) { clearTimeout(warmT); warmT = setTimeout(function () { idle(warmStep); }, ms); }
    function warmSoon() { if (!warmQ && warmGen !== sheetGen && O.checked && !glLost) warmLater(250); }
    function warmBusy() { var now = performance.now(); return looping || !!O.tween || O.touching || O.pressing || now - O.lastScrollAt < 400 || now - inputAt < 1000; }
    function warmTextures() {
      var list = [], seen = [];
      scene.traverse(function (o) {
        if (!o.isMesh) return;
        [].concat(o.material).forEach(function (m) {
          ["map", "emissiveMap", "alphaMap", "roughnessMap", "bumpMap"].forEach(function (k) { var t = m[k]; if (t && seen.indexOf(t) < 0) { seen.push(t); list.push(t); } });
        });
      });
      // the card's faces not on it right now: the printed verdict, swapped in as the card leaves its stand on the
      // first Next, and the two standing faces
      [cvMap, csMap, csvMap].forEach(function (t) { if (t && seen.indexOf(t) < 0) { seen.push(t); list.push(t); } });
      return list;
    }
    function warmStep() {
      if (glLost) return;
      if (!warmQ) { if (warmGen === sheetGen || !O.checked) return; warmQ = { gen: sheetGen, compiled: false, list: null }; }
      if (warmQ.gen !== sheetGen) { warmQ = null; warmSoon(); return; }
      if (warmBusy()) { warmLater(300); return; }
      if (!warmQ.compiled) { renderer.compile(scene, camera); warmQ.compiled = true; warmQ.list = warmTextures(); }
      else {
        var t = warmQ.list.shift(), im = t && t.image;
        // only a texture whose picture is complete: one still loading is sent when it is first drawn, as before
        if (t && t.version > 0 && im && im.complete !== false && (im.width || im.videoWidth)) renderer.initTexture(t);
      }
      if (!warmQ.list.length) { warmGen = warmQ.gen; warmQ = null; return; }
      idle(warmStep);
    }
    O.onCheck = function () {
      casters = null; shadowDirty = true;
      // a result landing on a desk already drawing (a check just run, an answer picked): the sheets' faces are painted
      // a few at a time after the answer is on screen (paintLazy, the top of the stack first and at full size), and
      // the standing face in a task of its own, so the answer is never held behind them. The page as it arrives
      // paints them all before its first frame, as before
      var later = frames > 0;
      paintCardFaces(later);
      if (later) { bigTop = true; O.wake(); return; }
      sheets.forEach(function (o) { paintOne(o); });
      // the top of the stack is seen close at the reading: its face at full size
      if (sheets.length) paintOne(sheets[sheets.length - 1], true);
      O.wake();
    };
    // a new result: its sheets, its card and its framings; the page is laid out again around them
    // a check running: at "found N", N blank sheets stand in the stack, placed, not moved (none under reduced motion).
    // The result's own sheets then take their places in the stack, already there
    var stagedN = 0;
    O.onStage = function (n) {
      if (O.reduce.matches || O.checked) return;
      var list = []; for (var i = 0; i < n; i++) list.push({ n: i + 1, group: "read", design: "", title: "", journal: "", year: "", pmid: "" });
      O.CHECK = { studies: list }; buildSheets(); sheets.forEach(function (o) { o.placed = true; }); stagedN = n; O.wake();
    };
    O.onLoad = function () {
      var staged = stagedN > 0; stagedN = 0;
      buildSheets(); if (staged) sheets.forEach(function (o) { o.placed = true; });
      cardPainted = false; csvKey = ""; cardKey = ""; pickKeys();
      O.checked = false; O.check();
      if (frames) setTimeout(paintLazy, 0);
      Ps = O.rawProgress(); O.wake();
    };
    // back to the claim box: the camera is at the first stop at once
    O.onHome = function () { if (stagedN) { stagedN = 0; O.CHECK = { studies: [] }; dropSheets(); } pickKeys(); Ps = 0; O.hoverN = 0; O.wake(); };
    buildSheets(); pickKeys();
    if (O.checked) { O.checked = false; O.check(); }
    O.layout(); O.updateWay();
    O.wake();
};
