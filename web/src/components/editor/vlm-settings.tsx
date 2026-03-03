/**
 * VLM Settings popover — controls DPI and crop margins.
 */
import { Settings } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import { useEditorStore } from '@/stores/editor-store';

export function VlmSettingsPopover() {
  const vlm = useEditorStore((s) => s.vlm);
  const setVlm = useEditorStore((s) => s.setVlm);

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="sm" title="VLM render settings (DPI, crop)">
          <Settings className="mr-1 size-3.5" />
          VLM Settings
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-64" align="start">
        <div className="space-y-4">
          <p className="text-xs font-semibold text-text-primary">
            VLM Render Settings
          </p>

          {/* DPI */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label>DPI (render resolution)</Label>
              <span className="text-xs text-text-secondary">{vlm.dpi} DPI</span>
            </div>
            <Slider
              min={72}
              max={400}
              step={1}
              value={[vlm.dpi]}
              onValueChange={([v]) => setVlm({ dpi: v })}
            />
          </div>

          {/* Crop Top */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label>Crop Top</Label>
              <span className="text-xs text-text-secondary">
                {Math.round(vlm.cropTop * 100)}%
              </span>
            </div>
            <Slider
              min={0}
              max={0.2}
              step={0.01}
              value={[vlm.cropTop]}
              onValueChange={([v]) => setVlm({ cropTop: v })}
            />
          </div>

          {/* Crop Bottom */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label>Crop Bottom</Label>
              <span className="text-xs text-text-secondary">
                {Math.round(vlm.cropBottom * 100)}%
              </span>
            </div>
            <Slider
              min={0}
              max={0.2}
              step={0.01}
              value={[vlm.cropBottom]}
              onValueChange={([v]) => setVlm({ cropBottom: v })}
            />
          </div>

          {/* Show overlay toggle */}
          <div className="flex items-center justify-between border-t border-border pt-3">
            <Label htmlFor="crop-overlay-toggle">
              Show crop overlay on PDF
            </Label>
            <Switch
              id="crop-overlay-toggle"
              checked={vlm.showCropOverlay}
              onCheckedChange={(checked) =>
                setVlm({ showCropOverlay: checked })
              }
            />
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}
