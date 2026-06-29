# 环境检查报错是啥情况
本程序有两个依赖，需要你自行安装。
1 yt-dlp (下载视频用的)
2 ffmpeg (把视频转换格式用的)
如果没有 yt-dlp  则无法下载。  
如果没有 ffmpeg 你有可能会下载到两个文件，一个音频，一个不带声音的视频。  

# 为什么我下载不了的视频？
```
[下载 1] 正在获取视频信息...
[下载 1] ERROR: [BiliBili] 1HyG76nEhW: Unable to download webpage: HTTP Error 412: Precondition Failed (caused by <HTTPError 412: Precondition Failed>)
[下载 1] 完成!
```
这种情况一般是需要使用cookie，因为网站具备反扒措施，不使用cookie的话，他认为你是机器人，就不给你下载。  
你可以参考下面的如何使用自己cookie下载视频来配置一下cookie。

# 如何使用自己的cookie下载视频
1. 去 chrome webstore 下载扩展： [Cookie-Editor](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm)
2. 打开B站，登录账号，点击扩展，右下角export（导出），选择 Netscape格式，然后点击程序 【编辑cookie】按钮，覆盖并保存txt文件 （参考下图）
3. 找一个会员视频，如 [迷宫饭](https://www.bilibili.com/bangumi/play/ep815459) ， 复制他的单集url如 `https://www.bilibili.com/bangumi/play/ep815459` ，然后勾选 使用cookie 下载即可

 <img width="3378" height="1780" alt="image" src="https://github.com/user-attachments/assets/aae50bc1-71d6-48c7-ba23-81dbe7932500" />

# 我下载的视频变成一个无声音视频+一个m4a纯音频文件了
如果遇到下载视频是变成了：一个音频文件 ， 一个无声视频。 则需要进行合并操作。
1. yt-dlp需要使用[ffmpeg](https://github.com/btbn/ffmpeg-builds/releases)进行视频合并，ffmpeg需要自行下载并添加到系统环境变量中
2. 下载后吧 ffmpeg.exe 等文件放到 c:/bin 文件夹中
3. 参考下图吧 c:/bin  加入环境变量目录
4. 打开cmd ，尝试输入 ffmpeg ，如果有返回一堆内容则代表ffmpeg已经配置ok了。
    <img width="2143" height="1762" alt="image" src="https://github.com/user-attachments/assets/52e222a6-58f8-4b87-b7f2-0f56e79f9534" />
