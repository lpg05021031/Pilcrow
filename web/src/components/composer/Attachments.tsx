import { useState } from 'react';
import { File as FileIcon, X } from 'lucide-react';
import { toast } from '../../lib/store';
import { bytes } from '../../lib/format';
import { Leave } from '../ui/bits';

interface Img {
  name: string;
  mediaType: string;
  data: string;
  url: string;
  size: number;
}

function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(',')[1] || '');
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}

/** Images and text files waiting to go with the next message: pasted, dropped or picked. */
export function useAttachments() {
  const [images, setImages] = useState<Img[]>([]);
  const [files, setFiles] = useState<{ name: string; text: string }[]>([]);
  const add = async (list: FileList | File[]) => {
    for (const f of Array.from(list)) {
      if (f.type.startsWith('image/')) {
        if (f.size > 8 * 1024 * 1024) {
          toast(`${f.name} is larger than 8 MB`, 'warning');
          continue;
        }
        const data = await readAsBase64(f);
        setImages((xs) => [...xs, { name: f.name || 'pasted image', mediaType: f.type, data, url: URL.createObjectURL(f), size: f.size }]);
      } else if (f.size < 400 * 1024) {
        const t = await f.text();
        if (/\u0000/.test(t.slice(0, 4000))) {
          toast(`${f.name} looks like a binary file`, 'warning');
          continue;
        }
        setFiles((xs) => [...xs, { name: f.name, text: t }]);
      } else toast(`${f.name} is too large to attach (400 KB max)`, 'warning');
    }
  };
  const clear = () => {
    setImages([]);
    setFiles([]);
  };
  const payload = () => ({ images: images.map((i) => ({ mediaType: i.mediaType, data: i.data })), files });
  const view =
    images.length || files.length ? (
      <div className="attachments">
        {images.map((img) => (
          <Leave key={img.url} className="attach-img" title={`${img.name} · ${bytes(img.size)}`} duration={180}>
            <img src={img.url} alt={img.name} />
            <button className="attach-x" onClick={() => setImages((xs) => xs.filter((x) => x !== img))} aria-label={`Remove ${img.name}`}>
              <X size={11} />
            </button>
          </Leave>
        ))}
        {files.map((f, k) => (
          <Leave key={`${f.name}:${k}`} className="attach-file" duration={180}>
            <FileIcon size={13} aria-hidden="true" /> <span className="ellipsis">{f.name}</span>
            <button onClick={() => setFiles((xs) => xs.filter((x) => x !== f))} aria-label={`Remove ${f.name}`}>
              <X size={11} />
            </button>
          </Leave>
        ))}
      </div>
    ) : null;
  return { count: images.length + files.length, add, clear, payload, view };
}
