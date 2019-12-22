#!/usr/bin/python3

import base64
import datetime
import email.parser
import email.policy
import email.utils
import getpass
import json
import os
import subprocess
import tempfile
import tkinter as tk
from tkinter import ttk
import tkinter.messagebox
import tkinter.scrolledtext
import tkinter.simpledialog
import urllib.parse
import urllib.request
import warnings

try:
  import html2text
  hasHtml2Text = True
except ImportError as e:
  warnings.warn("importing html2text failed, continuing without converting HTML to text", source=e)
  hasHtml2Text = False



class Server(object):
  def __init__(self, proxyUrl, proxyUsername, proxyPassword,
      serverUrl, serverUsername, serverPassword):
    self.proxyUrl = proxyUrl
    self.proxyUsername = proxyUsername
    self.proxyPassword = proxyPassword
    self.serverUrl = serverUrl
    self.serverUsername = serverUsername
    self.serverPassword = serverPassword

  def request(self, method, data):
    allData = {
          "version" : "1.0",
          "username" : self.proxyUsername,
          "password" : self.proxyPassword,
          "serverUrl" : self.serverUrl,
          "serverUsername" : self.serverUsername,
          "serverPassword" : self.serverPassword,
          "method" : method,
          "data" : data,
        }

    requestBody = urllib.parse.urlencode({"request" : json.dumps(allData)}).encode()
    request = urllib.request.Request(self.proxyUrl, data=requestBody)
    #print(requestBody)

    with urllib.request.urlopen(request) as f:
      assert f.getcode() == 200
      response = f.read()

    response = json.loads(response.decode())
    assert response["version"] == "1.0"
    assert response["status"] == 200
    responseData = response["data"]
    #print(responseData)

    return responseData



class Message(object):
  def __init__(self, mailbox, uid, header=None, emailObject=None, isUnread=False):
    self.mailbox = mailbox
    self.uid = uid
    self.header = header
    self.emailObject = emailObject
    self.isUnread = isUnread

  def __eq__(self, other):
    return ((self.mailbox, self.uid) == (other.mailbox, other.uid))

  def parseHeader(self, headerBase64):
    parser = email.parser.BytesHeaderParser(policy=email.policy.default)
    self.header = parser.parsebytes(base64.b64decode(headerBase64))

  def parseEmail(self, emailBase64):
    parser = email.parser.BytesParser(policy=email.policy.default)
    self.emailObject = parser.parsebytes(base64.b64decode(emailBase64))

  def getSubject(self):
    return self.header["Subject"]

  def getFrom(self):
    return email.utils.getaddresses(self.header.get_all("From", []))

  def getDate(self):
    return email.utils.parsedate_to_datetime(self.header["Date"])

  def getTo(self):
    return email.utils.getaddresses(self.header.get_all("To", []))

  def getCc(self):
    return email.utils.getaddresses(self.header.get_all("CC", []))

  def getBodyAsText(self):
    body = self.emailObject.get_body(["plain", "related", "html"])

    if body is not None:
      bodyAsText = body.get_content()

      if (body.get_content_type() == "text/html") and hasHtml2Text:
        bodyAsText = html2text.html2text(bodyAsText)
    elif self.emailObject.get_content_type() == "multipart/encrypted":
      for part in self.emailObject.walk():
        if part.get_content_type() == "application/octet-stream":
          bodyAsText = part.get_content().decode()
          break
    else:
      bodyAsText = ""

    return bodyAsText.replace("\r\n", "\n").replace("\r", "\n")

  @staticmethod
  def formatAddressesShort(addresses):
    return ", ".join([(x[0] if x[0] != "" else x[1]) for x in addresses])

  @staticmethod
  def formatAddressesLong(addresses):
    return ", ".join([("{} <{}>".format(*x) if x[0] != "" else x[1]) for x in addresses])

  @staticmethod
  def formatDateShort(date):
    return email.utils.localtime(date).strftime("%Y-%m-%d %H:%M")

  @staticmethod
  def formatDateLong(date):
    return email.utils.localtime(date).strftime("%A, %B %d, %Y, %H:%M").replace(" 0", " ")



class Mailbox(object):
  batchSize = 50

  def __init__(self, address, pop3Server, smtpServer=None, signature=None):
    self.address = address
    self.pop3Server = pop3Server
    self.smtpServer = smtpServer
    self.signature = signature
    self.messages = []

  @property
  def numberOfUnreadMessages(self):
    return len([x for x in self.messages if x.isUnread])

  def fetchNewMessages(self):
    newerThanUid = (self.messages[0].uid if len(self.messages) > 0 else None)
    setIsUnread = (len(self.messages) > 0)
    responseMessages = self.pop3Server.request("fetchNewMessages", {
        "batchSize" : Mailbox.batchSize, "newerThanUid" : newerThanUid})

    for responseMessage in reversed(responseMessages):
      newMessage = Message(self, responseMessage["uid"], isUnread=setIsUnread)
      newMessage.parseHeader(responseMessage["header"])
      self.messages.insert(0, newMessage)

  def fetchMoreOldMessages(self):
    if len(self.messages) == 0: return self.fetchNewMessages()
    olderThanUid = self.messages[-1].uid
    responseMessages = self.pop3Server.request("fetchOldMessages", {
        "batchSize" : Mailbox.batchSize, "olderThanUid" : olderThanUid})

    for responseMessage in responseMessages:
      oldMessage = Message(self, responseMessage["uid"])
      oldMessage.parseHeader(responseMessage["header"])
      self.messages.append(oldMessage)

  def fetchMessageBody(self, message):
    if message.emailObject is not None: return
    parser = email.parser.BytesParser(policy=email.policy.default)
    message.parseEmail(self.pop3Server.request("fetchMessageBody", {"uid" : message.uid}))



class JsonEncoder(json.JSONEncoder):
  def default(self, o):
    return (o.__dict__ if isinstance(o, (Server, Mailbox)) else super().default(o))

def jsonDecoderHook(o):
  if all(x in o for x in ("proxyUrl", "proxyUsername", "proxyPassword",
      "serverUrl", "serverUsername", "serverPassword")):
    return Server(o["proxyUrl"], o["proxyUsername"], o["proxyPassword"],
        o["serverUrl"], o["serverUsername"], o["serverPassword"])
  elif all(x in o for x in ("address", "pop3Server")):
    return Mailbox(o["address"], o["pop3Server"], signature=o.get("signature", None))
  else:
    return o



class ApplicationFrame(tk.Frame):
  def __init__(self, master=None, **kwargs):
    super().__init__(master, **kwargs)
    self.master = master

    iconPhotoImage = tk.PhotoImage(file=os.path.join(os.path.dirname(os.path.abspath(__file__)),
        "icon.png"))
    self.master.tk.call("wm", "iconphoto", self.master._w, iconPhotoImage)

  def addMenuEntry(self, menu, command, label, shortcut=None):
    menu.add_command(command=command, label=label, accelerator=shortcut)

    if shortcut is not None:
      sequenceParts = []

      for part in shortcut.split("+"):
        if len(part) == 1: part = part.lower()
        part = {"Ctrl" : "Control"}.get(part, part)
        sequenceParts.append(part)

      sequence = "<{}>".format("-".join(sequenceParts))
      self.bind_all(sequence, command)



class MainFrame(ApplicationFrame):
  def __init__(self, master=None, **kwargs):
    super().__init__(master, **kwargs)
    self.mailboxes = []

    try:
      with open("clientConfiguration.json", "r") as f:
        self.mailboxes = json.JSONDecoder(object_hook=jsonDecoderHook).decode(f.read())
    except:
      pass

    #print(JsonEncoder(indent=2).encode(self.mailboxes))

    self.pack(fill="both", expand=True)
    self.createWidgets()

  def getMailboxForEntry(self, entry):
    for mailbox in self.mailboxes:
      if id(mailbox) == int(entry): return mailbox

    return None

  def getMessageForEntry(self, entry):
    for mailbox in self.mailboxes:
      for message in mailbox.messages:
        if id(message) == int(entry): return message

    return None

  def getSelectedMailbox(self):
    entries = self.mailboxTreeview.selection()
    return (self.getMailboxForEntry(entries[0]) if len(entries) == 1 else None)

  def getSelectedMessage(self):
    entries = self.messageTreeview.selection()
    if len(entries) != 1: return None
    return (self.getMessageForEntry(entries[0]) if len(entries) == 1 else None)

  def getServerPassword(self, server):
    if server.serverPassword is None:
      server.serverPassword = tkinter.simpledialog.askstring("Enter POP3 Password",
          "Enter POP3 password for {}:".format(server.serverUrl), show="*")
      return (server.serverPassword is not None)
    else:
      return True

  def createWidgets(self):
    self.menuFrame = tk.Frame(self, bg=self["bg"])
    self.menuFrame.pack(fill="x")

    self.mailboxMenuButton = tk.Button(self.menuFrame, command=self.onMailboxMenuButtonClick,
        text="Mailbox", anchor="w", bg=self["bg"], relief="flat", borderwidth=0)
    self.mailboxMenuButton.pack(side="left")

    self.messageMenuButton = tk.Button(self.menuFrame, command=self.onMessageMenuButtonClick,
        text="Message", anchor="w", bg=self["bg"], relief="flat", borderwidth=0)
    self.messageMenuButton.pack(side="left")

    self.mailboxMenu = tk.Menu(self, tearoff=False, fg="#dddddd", bg="#444444", borderwidth=0,
        relief="flat", activeborderwidth=3)
    self.addMenuEntry(self.mailboxMenu, self.onFetchNewMessagesAllMailboxesClick,
        "Fetch New Messages (All Mailboxes)", "F5")
    self.addMenuEntry(self.mailboxMenu, self.onFetchNewMessagesClick,
        "Fetch New Messages", "Shift+F5")
    self.addMenuEntry(self.mailboxMenu, self.onFetchMoreOldMessagesClick,
        "Fetch More Old Messages", "Shift+F6")
    self.mailboxMenu.add_separator()
    self.addMenuEntry(self.mailboxMenu, self.onClearAllLocalDataClick, "Clear All Local Data")
    self.mailboxMenu.add_separator()
    self.addMenuEntry(self.mailboxMenu, self.onExitClick, "Exit", "Ctrl+Q")

    self.messageMenu = tk.Menu(self, tearoff=False, fg="#dddddd", bg="#444444", borderwidth=0,
        relief="flat", activeborderwidth=3)
    self.addMenuEntry(self.messageMenu, self.onOpenMessageClick, "Open", "Ctrl+O")
    #self.messageMenu.add_separator()
    #self.addMenuEntry(self.messageMenu, self.onNewMessageClick, "New", "Ctrl+N")
    #self.addMenuEntry(self.messageMenu, self.onReplyMessageClick, "Reply", "Ctrl+R")
    #self.addMenuEntry(self.messageMenu, self.onForwardMessageClick, "Forward", "Ctrl+L")
    #self.addMenuEntry(self.messageMenu, self.onEditAsNewMessageClick, "Edit As New", "Ctrl+E")

    self.panedWindow = tk.PanedWindow(self, orient="horizontal", sashwidth=5, border=0,
        background="#333333")
    self.panedWindow.pack(fill="both", expand=True)

    self.mailboxTreeview = ttk.Treeview(self.panedWindow, columns=["mailbox"], show=["headings"],
        selectmode="browse")
    self.mailboxTreeview.heading("mailbox", text="Mailbox", anchor="w")

    for mailbox in self.mailboxes:
      self.mailboxTreeview.insert("", "end", values=[mailbox.address], iid=str(id(mailbox)))

    self.mailboxTreeview.bind("<<TreeviewSelect>>", self.onMailboxTreeviewSelect)
    self.mailboxTreeview.bind("<Double-1>", self.onMailboxTreeviewDoubleClick)
    self.mailboxTreeview.tag_configure("unread", font=(None, 9, "bold"))

    self.messageTreeviewFrame = tk.Frame(self.panedWindow)

    self.messageTreeview = ttk.Treeview(self.messageTreeviewFrame,
        columns=["subject", "from", "date"], show=["headings"], selectmode="browse")
    self.messageTreeview.pack(side="left", fill="both", expand=True)
    self.messageTreeview.heading("subject", text="Subject", anchor="w")
    self.messageTreeview.column("subject", minwidth=80)
    self.messageTreeview.heading("from", text="From", anchor="w")
    self.messageTreeview.column("from", minwidth=80)
    self.messageTreeview.heading("date", text="Date", anchor="w")
    self.messageTreeview.column("date", minwidth=100, width=120)
    self.messageTreeview.bind("<Double-1>", self.onMessageTreeviewDoubleClick)
    self.messageTreeview.tag_configure("unread", font=(None, 9, "bold"))

    self.messageTreeviewScrollbar = ttk.Scrollbar(self.messageTreeviewFrame, orient="vertical",
        command=self.messageTreeview.yview)
    self.messageTreeviewScrollbar.pack(side="right", fill="y")
    self.messageTreeview.configure(yscrollcommand=self.messageTreeviewScrollbar.set)

    self.mailboxTreeview.selection_set([str(id(self.mailboxes[0]))])

    self.panedWindow.add(self.mailboxTreeview)
    self.panedWindow.add(self.messageTreeviewFrame)

    self.statusFrameOuter = tk.Frame(self, background="#1d1d1d")
    self.statusFrameOuter.pack(fill="x", pady=1)

    self.statusFrame = tk.Frame(self.statusFrameOuter, background=self.statusFrameOuter["background"])
    self.statusFrame.pack(fill="both", expand=True, padx=5, pady=5)

    self.statusProgressbar = ttk.Progressbar(self.statusFrame, orient="horizontal", length=100,
        mode="indeterminate")

    self.statusLabelLeft = ttk.Label(self.statusFrame, background="#1d1d1d",
        foreground="#dddddd")

    self.statusLabelRight = ttk.Label(self.statusFrame, background="#1d1d1d",
        foreground="#dddddd")
    self.statusLabelRight.pack(side="right", fill="x")

    self.updateStatusMenuWidgets()

    self.master.title("Mails")
    self.master.minsize(500, 300)
    self.panedWindow.paneconfig(self.mailboxTreeview, minsize=230)
    self.panedWindow.paneconfig(self.messageTreeviewFrame, minsize=200)

    self.messageFrames = []

  def updateMailboxTreeview(self):
    for mailbox in self.mailboxes:
      entry = str(id(mailbox))
      tags = list(self.mailboxTreeview.item(entry, "tags"))

      if ("unread" in tags) and (mailbox.numberOfUnreadMessages == 0):
        del tags[tags.index("unread")]
        self.mailboxTreeview.item(entry, tags=tags)
      elif ("unread" not in tags) and (mailbox.numberOfUnreadMessages > 0):
        tags.append("unread")
        self.mailboxTreeview.item(entry, tags=tags)

  def insertMessageInTreeview(self, message, pos):
    values = [message.getSubject(), Message.formatAddressesShort(message.getFrom()),
        Message.formatDateShort(message.getDate())]
    tags = [("unread",) if message.isUnread else ()]
    self.messageTreeview.insert("", pos, values=values, iid=str(id(message)), tags=tags)

  def updateMessageTreeview(self):
    oldMessageIds = [int(x) for x in self.messageTreeview.get_children()]
    newMessages = self.getSelectedMailbox().messages
    newMessageIds = [id(x) for x in newMessages]
    newPos = -1

    for oldMessageId in oldMessageIds:
      if oldMessageId in newMessageIds:
        newPos2 = newMessageIds.index(oldMessageId)
        assert newPos2 > newPos

        for pos in range(newPos + 1, newPos2):
          self.insertMessageInTreeview(newMessages[pos], pos)

        newPos = newPos2
      else:
        self.messageTreeview.delete(str(oldMessageId))

    for pos in range(newPos + 1, len(newMessages)):
      self.insertMessageInTreeview(newMessages[pos], "end")

  def updateStatusMenuWidgets(self, status=None):
    if status is not None:
      text = {
            "fetchingMessages" : "Fetching Messages...",
            "fetchingMessageBody" : "Fetching Message Body...",
          }[status]
      self.statusLabelLeft.configure(text=text)
      self.statusProgressbar["value"] = 0
      self.statusProgressbar.start(8)
      self.statusLabelLeft.pack_forget()
      self.statusProgressbar.pack(side="left", padx=5)
      self.statusLabelLeft.pack(side="left", fill="x")
      for i in [0, 1, 2, 4]: self.mailboxMenu.entryconfig(i, state="disabled")
      for i in [0]: self.messageMenu.entryconfig(i, state="disabled")
    else:
      self.statusLabelLeft.configure(text="")
      self.statusProgressbar.pack_forget()
      messageMenuState = ("normal" if self.getSelectedMessage() is not None else "disabled")
      for i in [0, 1, 2, 4]: self.mailboxMenu.entryconfig(i, state="normal")
      for i in [0]: self.messageMenu.entryconfig(i, state=messageMenuState)

    mailbox = self.getSelectedMailbox()
    self.statusLabelRight.configure(text="Unread: {}  Total: {}".format(
        mailbox.numberOfUnreadMessages, len(mailbox.messages)))
    self.update()

  def onMailboxMenuButtonClick(self, event=None):
    self.mailboxMenu.tk_popup(self.mailboxMenuButton.winfo_rootx(),
        self.mailboxMenuButton.winfo_rooty() + self.mailboxMenuButton.winfo_height())

  def onMessageMenuButtonClick(self, event=None):
    self.messageMenu.tk_popup(self.messageMenuButton.winfo_rootx(),
        self.messageMenuButton.winfo_rooty() + self.messageMenuButton.winfo_height())

  def onFetchNewMessagesAllMailboxesClick(self, event=None):
    self.updateStatusMenuWidgets(status="fetchingMessages")

    try:
      for mailbox in self.mailboxes:
        try:
          if self.getServerPassword(mailbox.pop3Server): mailbox.fetchNewMessages()
        except:
          mailbox.pop3Server.serverPassword = None
          raise

      self.updateMailboxTreeview()
      self.updateMessageTreeview()
    finally:
      self.updateStatusMenuWidgets()

  def onFetchNewMessagesClick(self, event=None):
    mailbox = self.getSelectedMailbox()

    if mailbox is not None:
      self.updateStatusMenuWidgets(status="fetchingMessages")

      try:
        if self.getServerPassword(mailbox.pop3Server):
          mailbox.fetchNewMessages()
          self.updateMailboxTreeview()
          self.updateMessageTreeview()
      except:
        mailbox.pop3Server.serverPassword = None
        raise
      finally:
        self.updateStatusMenuWidgets()

  def onFetchMoreOldMessagesClick(self, event=None):
    mailbox = self.getSelectedMailbox()

    if mailbox is not None:
      self.updateStatusMenuWidgets(status="fetchingMessages")

      try:
        if self.getServerPassword(mailbox.pop3Server):
          mailbox.fetchMoreOldMessages()
          self.updateMailboxTreeview()
          self.updateMessageTreeview()
      except:
        mailbox.pop3Server.serverPassword = None
        raise
      finally:
        self.updateStatusMenuWidgets()

  def onClearAllLocalDataClick(self, event=None):
    for mailbox in self.mailboxes:
      mailbox.messages.clear()
      if mailbox.pop3Server is not None: mailbox.pop3Server.serverPassword = None
      if mailbox.smtpServer is not None: mailbox.smtpServer.serverPassword = None

    self.updateMailboxTreeview()
    self.updateMessageTreeview()
    self.updateStatusMenuWidgets()

  def onExitClick(self, event=None):
    self.master.destroy()

  def onOpenMessageClick(self, event=None, message=None):
    if message is None:
      message = self.getSelectedMessage()
      if message is None: return

    self.updateStatusMenuWidgets(status="fetchingMessageBody")

    try:
      if message.emailObject is None:
        if not self.getServerPassword(message.mailbox.pop3Server): return
        message.mailbox.fetchMessageBody(message)

      if message.isUnread:
        message.isUnread = False
        tags = list(self.messageTreeview.item(str(id(message)), "tags"))
        del tags[tags.index("unread")]
        self.messageTreeview.item(str(id(message)), tags=tags)
        if message.mailbox.numberOfUnreadMessages == 0: self.updateMailboxTreeview()

      toplevel = tk.Toplevel(self.master)
      self.messageFrames.append(MessageFrame(toplevel, message))
      toplevel.focus()
    finally:
      self.updateStatusMenuWidgets()

  #def onNewMessageClick(self, event=None):
  #  pass
  #
  #def onReplyMessageClick(self, event=None):
  #  pass
  #
  #def onForwardMessageClick(self, event=None):
  #  pass
  #
  #def onEditAsNewMessageClick(self, event=None):
  #  pass

  def onMailboxTreeviewSelect(self, event=None):
    self.updateMessageTreeview()
    self.updateStatusMenuWidgets()

  def onMailboxTreeviewDoubleClick(self, event=None):
    self.onFetchNewMessagesClick()

  def onMessageTreeviewDoubleClick(self, event=None):
    message = self.messageTreeview.identify("item", event.x, event.y)
    if message != "": self.onOpenMessageClick(message=self.getMessageForEntry(message))



class MessageFrame(ApplicationFrame):
  def __init__(self, master=None, message=None, **kwargs):
    super().__init__(master, **kwargs)
    self.message = message
    self.messageBody = message.getBodyAsText()
    self.pack(fill="both", expand=True)
    self.createWidgets()

  @staticmethod
  def disableText(text):
    text.config(state="disabled")
    text.bind("<1>", lambda event: text.focus_set())

  @staticmethod
  def insertAddressesIntoText(text, addresses):
    pos = 0
    formatPos = (lambda x: "1.{}".format(x))

    for i, address in enumerate(addresses):
      if i > 0:
        text.insert("insert", "  ")
        pos += 2

      if address[0] != "":
        text.insert("insert", "{} ".format(address[0]))
        pos += len(address[0]) + 1
        text.insert("insert", "<{}>".format(address[1]))
        text.tag_add("plainAddress", formatPos(pos), formatPos(pos + len(address[1]) + 2))
        pos += len(address[1]) + 2
      else:
        text.insert("insert", address[1])
        pos += len(address[1])

  def createWidgets(self):
    self.menuFrame = tk.Frame(self, bg=self["bg"])
    self.menuFrame.pack(fill="x")

    self.messageMenuButton = tk.Button(self.menuFrame, command=self.onMessageMenuButtonClick,
        text="Message", anchor="w", bg=self["bg"], relief="flat", borderwidth=0)
    self.messageMenuButton.pack(side="left")

    self.messageMenu = tk.Menu(self, tearoff=False, fg="#dddddd", bg="#444444", borderwidth=0,
        relief="flat", activeborderwidth=3)
    self.addMenuEntry(self.messageMenu, self.onVerifySignatureMessageClick, "Verify Signature",
        "Ctrl+U")
    self.addMenuEntry(self.messageMenu, self.onDecryptMessageClick, "Decrypt", "Ctrl+D")
    self.messageMenu.add_separator()
    self.addMenuEntry(self.messageMenu, self.onCloseMessageClick, "Close", "Ctrl+W")

    self.contentFrame = tk.Frame(self, bg="#333333")
    self.contentFrame.pack(fill="both", expand=True)

    labelKwargs = {"background" : self.contentFrame["bg"], "foreground" : "#dddddd"}
    textKwargs = {"height" : 1, "wrap" : "none", "font" : "TkDefaultFont", **labelKwargs}
    row = 0

    self.dateLabel = ttk.Label(self.contentFrame, text="Date:", **labelKwargs)
    self.dateLabel.grid(row=row, column=0, sticky="w")

    self.dateText = tk.Text(self.contentFrame, **textKwargs)
    self.dateText.grid(row=row, column=1, sticky="we")
    self.dateText.insert("insert", Message.formatDateLong(self.message.getDate()))
    MessageFrame.disableText(self.dateText)
    row += 1

    self.fromLabel = ttk.Label(self.contentFrame, text="From:", **labelKwargs)
    self.fromLabel.grid(row=row, column=0, sticky="w")

    self.fromText = tk.Text(self.contentFrame, **textKwargs)
    self.fromText.grid(row=row, column=1, sticky="we")
    self.fromText.tag_configure("plainAddress", foreground="#888888")
    MessageFrame.insertAddressesIntoText(self.fromText, self.message.getFrom())
    MessageFrame.disableText(self.fromText)
    row += 1

    self.subjectLabel = ttk.Label(self.contentFrame, text="Subject:", **labelKwargs)
    self.subjectLabel.grid(row=row, column=0, sticky="w")

    self.subjectText = tk.Text(self.contentFrame, **textKwargs)
    self.subjectText.grid(row=row, column=1, sticky="we")
    self.subjectText.insert("insert", self.message.getSubject())
    MessageFrame.disableText(self.subjectText)
    row += 1

    self.toLabel = ttk.Label(self.contentFrame, text="To:", **labelKwargs)
    self.toLabel.grid(row=row, column=0, sticky="w")

    self.toText = tk.Text(self.contentFrame, **textKwargs)
    self.toText.grid(row=row, column=1, sticky="we")
    self.toText.tag_configure("plainAddress", foreground="#888888")
    MessageFrame.insertAddressesIntoText(self.toText, self.message.getTo())
    MessageFrame.disableText(self.toText)
    row += 1

    cc = self.message.getCc()

    if len(cc) > 0:
      self.ccLabel = ttk.Label(self.contentFrame, text="Cc:", **labelKwargs)
      self.ccLabel.grid(row=row, column=0, sticky="w")

      self.ccText = tk.Text(self.contentFrame, **textKwargs)
      self.ccText.grid(row=row, column=1, sticky="we")
      self.ccText.tag_configure("plainAddress", foreground="#888888")
      MessageFrame.insertAddressesIntoText(self.ccText, cc)
      MessageFrame.disableText(self.ccText)

      row += 1

    self.messageScrolledText = tkinter.scrolledtext.ScrolledText(self.contentFrame,
        wrap="word", bg="#1d1d1d", fg="#dddddd", font="\"Ubuntu Mono\" 12", borderwidth=0)
    self.messageScrolledText.grid(row=row, column=0, columnspan=2, sticky="wens")
    self.messageScrolledText.insert("insert", self.messageBody)
    self.messageScrolledText.config(state="disabled")
    self.messageScrolledText.bind("<1>", lambda event: self.messageScrolledText.focus_set())

    for i in range(row):
      self.contentFrame.grid_rowconfigure(i, pad=5)

    self.contentFrame.grid_rowconfigure(row, weight=1)

    self.contentFrame.grid_columnconfigure(0, pad=5)
    self.contentFrame.grid_columnconfigure(1, weight=1, pad=5)

    self.master.title("{} - Mails".format(self.message.getSubject()))
    self.master.minsize(400, 300)

  def onMessageMenuButtonClick(self, event=None):
    self.messageMenu.tk_popup(self.messageMenuButton.winfo_rootx(),
        self.messageMenuButton.winfo_rooty() + self.messageMenuButton.winfo_height())

  def onVerifySignatureMessageClick(self, event=None):
    with tempfile.TemporaryDirectory() as tempDir:
      messagePath = os.path.join(tempDir, "message.gpg")
      with open(messagePath, "w") as f: f.write(self.message.getBodyAsText())
      process = subprocess.run(["gpg", "--batch", "--yes", "--verify", messagePath],
          check=False, stderr=subprocess.PIPE)

    textFormat = "{{}}\n\nDetails:\n{}".format(process.stderr.decode())

    if process.returncode == 0:
      tkinter.messagebox.showinfo("Signature check successful", textFormat.format(
          "Good signature."))
    elif process.returncode == 1:
      tkinter.messagebox.showerror("Signature check failed", textFormat.format(
          "Bad signature!"))
    else:
      tkinter.messagebox.showerror("Signature check failed", textFormat.format(
          "Error while checking signature!"))

  def onDecryptMessageClick(self, event=None):
    #gnupgPassword = tkinter.simpledialog.askstring("Enter GnuPG Password",
    #    "Enter GnuPG password of secret keyring:", show="*")
    #if gnupgPassword is None: return

    with tempfile.TemporaryDirectory() as tempDir:
      messagePath = os.path.join(tempDir, "message.gpg")
      with open(messagePath, "w") as f: f.write(self.message.getBodyAsText())
      #process = subprocess.run(["gpg", "--batch", "--yes", "--pinentry-mode", "loopback",
      #      "--passphrase-fd", "0", "--decrypt", messagePath],
      #    check=False, input=gnupgPassword.encode(), stdout=subprocess.PIPE,
      #    stderr=subprocess.PIPE)
      #gnupgPassword = ""
      process = subprocess.run(["gpg", "--batch", "--yes", "--decrypt", messagePath],
          check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if process.returncode == 0:
      self.messageScrolledText.config(state="normal")
      self.messageScrolledText.delete("1.0", "end")
      self.messageScrolledText.insert("insert", process.stdout.decode())
      self.messageScrolledText.config(state="disabled")
    else:
      tkinter.messagebox.showerror("Decrypt failed",
          "Error while decrypting!\n\nDetails:\n{}".format(process.stderr.decode()))

  def onCloseMessageClick(self, event=None):
    self.master.destroy()



def main():
  root = tk.Tk()

  root.style = ttk.Style()
  root.style.theme_use("default")

  root.style.configure("Frame", background=None)
  root.style.configure("Treeview", background="#1d1d1d", fieldbackground="#1d1d1d",
      foreground="#dddddd", borderwidth=0)
  root.style.configure("Treeview.Heading", background="#333333", foreground="#dddddd",
      padding=3, relief="flat")
  root.style.map("Treeview.Heading", background=[("pressed", "#444444")])

  mainFrame = MainFrame(master=root)
  mainFrame.mainloop()



if __name__ == "__main__":
  main()
