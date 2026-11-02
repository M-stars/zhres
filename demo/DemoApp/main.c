/* DemoApp —— zhres 演示用纯 Win32 英文程序。
 *
 * 所有英文界面文本集中定义在 app.rc（菜单/对话框资源 + STRINGTABLE）。
 * 用途：验证 zhres 的 提取 → 翻译 → 回写 全流程（界面持久变中文）。
 */

#include <windows.h>
#include <commctrl.h>
#include "resource.h"

#define ID_STATUSBAR 2000

static HWND g_status = NULL;

INT_PTR CALLBACK AboutDlgProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp);

static void ShowAbout(HWND hwnd)
{
    DialogBox(GetModuleHandle(NULL), MAKEINTRESOURCE(IDD_ABOUT), hwnd,
              AboutDlgProc);
}

static void ShowMessage(HWND hwnd, UINT stringId, UINT captionId)
{
    WCHAR text[512];
    WCHAR caption[128];
    LoadStringW(GetModuleHandle(NULL), stringId, text, 512);
    LoadStringW(GetModuleHandle(NULL), captionId, caption, 128);
    MessageBoxW(hwnd, text, caption, MB_OK | MB_ICONINFORMATION);
}

INT_PTR CALLBACK AboutDlgProc(HWND dlg, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_INITDIALOG:
        return TRUE;
    case WM_COMMAND:
        if (LOWORD(wp) == IDOK || LOWORD(wp) == IDCANCEL) {
            EndDialog(dlg, LOWORD(wp));
            return TRUE;
        }
        break;
    }
    return FALSE;
}

static LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_CREATE: {
        g_status = CreateWindowExW(0, STATUSCLASSNAMEW, NULL,
                                   WS_CHILD | WS_VISIBLE | SBARS_SIZEGRIP,
                                   0, 0, 0, 0, hwnd, (HMENU)ID_STATUSBAR,
                                   GetModuleHandle(NULL), NULL);
        WCHAR ready[128];
        LoadStringW(GetModuleHandle(NULL), IDS_READY, ready, 128);
        SendMessageW(g_status, SB_SETTEXTW, 0, (LPARAM)ready);
        return 0;
    }
    case WM_SIZE:
        SendMessageW(g_status, WM_SIZE, 0, 0);
        return 0;
    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case IDM_FILE_NEW:
            ShowMessage(hwnd, IDS_NEW_MSG, IDS_INFO);
            return 0;
        case IDM_FILE_OPEN:
            ShowMessage(hwnd, IDS_OPEN_MSG, IDS_INFO);
            return 0;
        case IDM_FILE_SAVE:
        case IDM_FILE_SAVEAS:
            ShowMessage(hwnd, IDS_SAVE_MSG, IDS_INFO);
            return 0;
        case IDM_EDIT_UNDO:
            ShowMessage(hwnd, IDS_UNDO_MSG, IDS_INFO);
            return 0;
        case IDM_EDIT_CUT:
            ShowMessage(hwnd, IDS_CUT_MSG, IDS_INFO);
            return 0;
        case IDM_EDIT_COPY:
            ShowMessage(hwnd, IDS_COPY_MSG, IDS_INFO);
            return 0;
        case IDM_EDIT_PASTE:
            ShowMessage(hwnd, IDS_PASTE_MSG, IDS_INFO);
            return 0;
        case IDM_EDIT_SELECTALL:
            ShowMessage(hwnd, IDS_SELECTALL_MSG, IDS_INFO);
            return 0;
        case IDM_APP_EXIT:
            DestroyWindow(hwnd);
            return 0;
        case IDM_HELP_ABOUT:
            ShowAbout(hwnd);
            return 0;
        }
        break;
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

int WINAPI WinMain(HINSTANCE hInst, HINSTANCE prev, LPSTR cmdline, int show)
{
    static const WCHAR CLASS_NAME[] = L"DemoAppWindow";
    WNDCLASSW wc = {0};
    wc.lpfnWndProc = WndProc;
    wc.hInstance = hInst;
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.hIcon = LoadIconW(hInst, MAKEINTRESOURCEW(IDI_APP));
    wc.lpszClassName = CLASS_NAME;
    RegisterClassW(&wc);

    HWND hwnd = CreateWindowExW(0, CLASS_NAME, L"DemoApp",
                                WS_OVERLAPPEDWINDOW, CW_USEDEFAULT,
                                CW_USEDEFAULT, 640, 420, NULL, NULL, hInst,
                                NULL);
    if (!hwnd)
        return 1;
    ShowWindow(hwnd, show);
    UpdateWindow(hwnd);

    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
    return (int)msg.wParam;
}
