/**
 * CodeMirror 6 Markdown editor component.
 *
 * Exposes imperative methods to get/set content and undo
 * via a forwarded ref (EditorHandle).
 */
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
} from 'react';

import {
  crosshairCursor,
  drawSelection,
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  keymap,
  lineNumbers,
  rectangularSelection,
} from '@codemirror/view';
import { EditorState as CmEditorState } from '@codemirror/state';
import { markdown } from '@codemirror/lang-markdown';
import {
  defaultKeymap,
  history,
  historyKeymap,
  undo,
} from '@codemirror/commands';
import { oneDark } from '@codemirror/theme-one-dark';
import {
  defaultHighlightStyle,
  foldGutter,
  syntaxHighlighting,
} from '@codemirror/language';
import { search, searchKeymap } from '@codemirror/search';

export interface EditorHandle {
  getContent(): string;
  setContent(text: string): void;
  getSelection(): { from: number; to: number; text: string } | null;
  undo(): void;
  getView(): EditorView | null;
}

interface MarkdownEditorProps {
  initialContent?: string;
  onUpdate?: (content: string) => void;
}

export const MarkdownEditor = forwardRef<EditorHandle, MarkdownEditorProps>(
  ({ initialContent = '', onUpdate }, ref) => {
    const hostRef = useRef<HTMLDivElement>(null);
    const viewRef = useRef<EditorView | null>(null);

    useImperativeHandle(ref, () => ({
      getContent() {
        return viewRef.current?.state.doc.toString() ?? '';
      },
      setContent(text: string) {
        const view = viewRef.current;
        if (!view) return;
        view.dispatch({
          changes: { from: 0, to: view.state.doc.length, insert: text },
        });
      },
      getSelection() {
        const view = viewRef.current;
        if (!view) return null;
        const sel = view.state.selection.main;
        if (sel.from === sel.to) return null;
        return {
          from: sel.from,
          to: sel.to,
          text: view.state.sliceDoc(sel.from, sel.to),
        };
      },
      undo() {
        const view = viewRef.current;
        if (view) undo(view);
      },
      getView() {
        return viewRef.current;
      },
    }));

    useEffect(() => {
      if (!hostRef.current) return;

      const view = new EditorView({
        parent: hostRef.current,
        state: CmEditorState.create({
          doc: initialContent,
          extensions: [
            lineNumbers(),
            highlightActiveLineGutter(),
            highlightActiveLine(),
            highlightSpecialChars(),
            drawSelection(),
            rectangularSelection(),
            crosshairCursor(),
            foldGutter(),
            history(),
            search(),
            markdown(),
            syntaxHighlighting(defaultHighlightStyle),
            oneDark,
            keymap.of([
              ...defaultKeymap,
              ...historyKeymap,
              ...searchKeymap,
            ]),
            EditorView.lineWrapping,
            EditorView.updateListener.of((v) => {
              if (v.docChanged && onUpdate) {
                onUpdate(v.state.doc.toString());
              }
            }),
          ],
        }),
      });

      viewRef.current = view;

      return () => {
        view.destroy();
        viewRef.current = null;
      };
      // Only create once — initialContent handled via setContent after load
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    return (
      <div
        ref={hostRef}
        className="flex-1 overflow-hidden [&_.cm-editor]:h-full [&_.cm-editor]:text-[13px] [&_.cm-scroller]:font-mono"
      />
    );
  },
);

MarkdownEditor.displayName = 'MarkdownEditor';
